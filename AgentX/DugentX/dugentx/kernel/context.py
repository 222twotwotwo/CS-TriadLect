"""上下文：一个服务的仓库。

dsh 用 Cordis 的 `ctx`。核心想法只有一条：

> **插件靠 `ctx.<key>` 拿服务，从不 import 具体实现。**

所以换一个 LLM adapter、换一个文件系统、换一个会话存储，
不需要改任何调用方——它们只认 `llm`、`fs`、`sessions` 这几个键。

DugentX 在 dsh 的基础上做了一处**刻意的简化**，并且写在这里以免误解：
Cordis 的 `inject` 是响应式的——服务晚到，插件会等着被唤醒。
DugentX 不做等待，而是**在装载前就把顺序排好**（见 `loader.py` 的
拓扑排序），缺服务就在装载时大声失败。少了一个响应式调度器，
换来的是一条更好查的规则：跑到一半才发现缺服务，说明配置写错了。
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from dugentx.kernel.effect import Disposer, EffectScope
from dugentx.kernel.errors import PluginError, ServiceNotFound
from dugentx.kernel.events import EventBus, Listener

if TYPE_CHECKING:
    from dugentx.kernel.plugin import Plugin


class Context:
    """服务仓库 + 一组可撤销的注册。"""

    def __init__(
        self,
        name: str = "root",
        *,
        services: dict[str, Any] | None = None,
        events: EventBus | None = None,
        scope: EffectScope | None = None,
        parent: Context | None = None,
    ) -> None:
        self.name = name
        self.parent = parent
        self._services: dict[str, Any] = services if services is not None else {}
        self.events: EventBus = events if events is not None else EventBus()
        if scope is not None:
            self.scope = scope
        elif parent is not None:
            self.scope = parent.scope.child(name)
        else:
            self.scope = EffectScope(name)

    # ---------------------------------------------------------------- 服务

    def provide(self, key: str, instance: Any) -> Disposer:
        """声明一个服务。返回的 disposer 会把它撤下来。"""
        if key in self._services:
            previous = type(self._services[key]).__name__
            raise PluginError(
                f"服务 {key!r} 已经被 {previous} 占用了；"
                f"同一个键只能有一个提供者（这正是「换掉实现」的方式："
                f"改配置，不是叠加）"
            )
        self._services[key] = instance

        def dispose() -> None:
            if self._services.get(key) is instance:
                del self._services[key]

        return self.scope.add(dispose, label=f"service:{key}")

    def get(self, key: str, default: Any = None) -> Any:
        return self._services.get(key, default)

    def has(self, *keys: str) -> bool:
        return all(k in self._services for k in keys)

    def service(self, key: str) -> Any:
        """取一个服务；没有就大声报错，不回退到默认实现。"""
        try:
            return self._services[key]
        except KeyError:
            raise ServiceNotFound(key, list(self._services)) from None

    def services(self) -> dict[str, Any]:
        return dict(self._services)

    def require(self, *keys: str) -> None:
        missing = [k for k in keys if k not in self._services]
        if missing:
            raise ServiceNotFound(missing[0], list(self._services))

    def __getattr__(self, key: str) -> Any:
        """`ctx.tools` 这样的语法糖。

        只对**已注册的服务键**生效；其他属性照常抛 AttributeError，
        所以 `hasattr` 仍然可信，拼错的键不会静默变成 None。
        """
        if key.startswith("_"):
            raise AttributeError(key)
        services = self.__dict__.get("_services", {})
        if key in services:
            return services[key]
        raise AttributeError(
            f"{key!r} 不是当前上下文里的服务；可用：{sorted(services) or '（空）'}"
        )

    # ---------------------------------------------------------------- 注册

    def effect(self, fn: Callable[[Context], Disposer | None], *, label: str = "") -> Disposer:
        """跑一段注册代码，并记住怎么撤销它。

        `fn` 可以返回自己的 disposer；不返回就只记一个空操作。
        """
        inner = fn(self)
        if inner is not None and not callable(inner):
            raise PluginError(f"effect {label or fn!r} 返回了非 callable：{inner!r}")
        return self.scope.add(
            inner or (lambda: None), label=label or getattr(fn, "__name__", "effect")
        )

    def on(
        self,
        event: str,
        listener: Listener,
        *,
        prepend: bool = False,
        once: bool = False,
    ) -> Disposer:
        """注册事件监听器，返回撤销它的 disposer。"""
        return self.scope.add(
            self.events.on(event, listener, prepend=prepend, once=once),
            label=f"on:{event}",
        )

    # ---------------------------------------------------------------- 子上下文

    def child(self, name: str) -> Context:
        """开一个子上下文。

        服务是共享的（换实现是全局的事），但注册是子上下文自己的：
        子上下文关闭时，它挂上去的服务、监听器、工具一起消失，
        父上下文和兄弟插件不受影响。运行期动态挂载就靠这个。
        """
        return Context(name, services=self._services, events=self.events, parent=self)

    async def mount(self, plugin: Plugin) -> Disposer:
        """装载一个插件，返回把它拔下来的 disposer。

        两件事这里是**对称**的：装载建立一个新的子作用域，
        卸载就是把那个作用域整个撤销。所以插件不需要写 `uninstall`，
        它只需要保证每一笔注册都走了 `ctx.provide` / `ctx.on` / `ctx.effect`。
        """
        missing = [k for k in plugin.inject if k not in self._services]
        if missing:
            raise PluginError(
                f"插件 {plugin.name!r} 需要 {missing}，但装载时还没有；"
                f"当前可用：{sorted(self._services)}"
            )

        child = self.child(f"plugin:{plugin.name}")
        child.plugin_name = plugin.name  # type: ignore[attr-defined]
        try:
            result = plugin.apply(child, plugin.config)
            if inspect.isawaitable(result):
                await result
        except BaseException:
            child.scope.dispose()
            raise
        return child.scope.dispose

    def dispose(self) -> None:
        self.scope.dispose()

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<Context {self.name} services={sorted(self._services)}>"
