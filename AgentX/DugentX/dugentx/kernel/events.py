"""事件总线：DugentX 的扩展点就在这里。

dsh 把事件分成五种派发方式，这不是分类癖，而是**公共契约**：
一个事件是「通知」还是「中间件」，调用方必须一眼看出来。
DugentX 原样保留这五种：

| 方式        | 是否 await | 顺序         | 有返回值 | 用来做什么                     |
|-------------|-----------|--------------|---------|--------------------------------|
| `emit`      | 否        | 注册顺序      | 否      | 观察：日志、计数、指标          |
| `waterfall` | 否        | 注册顺序，可包裹 | 是      | 中间件：策略、改写、短路         |
| `parallel`  | 是        | 并行          | 否      | 互不相干的多件收尾工作           |
| `serial`    | 是        | 注册顺序      | 是      | 依次询问，拿到结果               |
| `bail`      | 否        | 注册顺序      | 是      | 第一个给出答案的胜出             |

**waterfall 是这里的重点**：它实现的是「环绕中间件」。监听器拿到
`(*args, next)`，调用 `next()` 把（可能被改写过的）参数交给下一个监听器；
不调 `next()` 直接返回，就是短路，后面的监听器和真正的实现都不会跑。
`agent/pre-step`、`agent/request`、`llm/stream`、`tools/pre-execute`
都是 waterfall——权限拦截、上下文注入、请求改写全都挂在这上面。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from dugentx.kernel.errors import DuGentXError

Disposer = Callable[[], None]
Listener = Callable[..., Any]

WATERFALL = "waterfall"
"""标记：这个监听器要按 waterfall 语义调用（额外收到一个 next）。"""

ALLOWED_MODES: frozenset[str] = frozenset({"emit", "waterfall", "parallel", "serial", "bail"})
"""内核认识的派发方式。

写成常量而不是散在校验里，因为它是**公共契约的一部分**：
校验、文档生成器、以及任何想知道「一共有几种」的东西都该读同一个集合。
两边各写一份的话，加第五种时总有一边会忘。
"""


@dataclass(slots=True)
class _Registration:
    listener: Listener
    prepend: bool
    once: bool = False
    alive: bool = True


class EventBus:
    """按名字分发事件。每个注册都返回一个 disposer，卸载插件时会自动撤销。

    传入 `catalog`（事件名 → 派发方式）后，总线会**在派发时校验**：
    用错方式发事件、或者发一个目录里没有的名字，都会当场报错。
    事件名写错是静默失败——监听器不触发，没有任何提示——
    所以这里宁可吵一点。
    """

    def __init__(self, catalog: dict[str, str] | None = None) -> None:
        self._listeners: dict[str, list[_Registration]] = {}
        self._catalog: dict[str, str] = dict(catalog or {})

    def _check(self, name: str, mode: str) -> None:
        if not self._catalog:
            return
        declared = self._catalog.get(name)
        if declared is None:
            raise DuGentXError(
                f"事件 {name!r} 不在事件目录里。"
                f"写错事件名不会有任何提示，所以这里直接拦住。"
                f"要新增事件，先写进 dugentx/events.py。"
            )
        if declared != mode:
            raise DuGentXError(
                f"事件 {name!r} 声明的派发方式是 {declared!r}，"
                f"却用 {mode!r} 发了出去。派发方式是公共契约的一部分："
                f"调用方要能一眼看出它是通知还是中间件。"
            )

    # ---------------------------------------------------------------- 注册

    def on(
        self,
        name: str,
        listener: Listener,
        *,
        prepend: bool = False,
        once: bool = False,
    ) -> Disposer:
        """注册一个监听器，返回撤销它的 disposer。"""
        reg = _Registration(listener=listener, prepend=prepend, once=once)
        bucket = self._listeners.setdefault(name, [])
        if prepend:
            bucket.insert(0, reg)
        else:
            bucket.append(reg)

        def dispose() -> None:
            reg.alive = False
            if bucket.count(reg):
                bucket.remove(reg)

        return dispose

    def listeners(self, name: str) -> list[Listener]:
        return [r.listener for r in self._listeners.get(name, []) if r.alive]

    def _take(self, name: str) -> list[_Registration]:
        """取出仍然有效的监听器；`once` 的顺手注销掉。"""
        bucket = self._listeners.get(name, [])
        alive = [r for r in bucket if r.alive]
        survivors = []
        for reg in alive:
            if reg.once:
                reg.alive = False
            else:
                survivors.append(reg)
        if len(survivors) != len(bucket):
            self._listeners[name] = survivors
        return alive

    # ---------------------------------------------------------------- 派发

    def emit(self, name: str, *args: Any) -> None:
        """通知：谁关心谁处理，返回值被丢掉，异常照常往上抛。"""
        self._check(name, "emit")
        for reg in self._take(name):
            reg.listener(*args)

    async def parallel(self, name: str, *args: Any) -> None:
        """并行：所有监听器同时跑，等最慢的那个。"""
        self._check(name, "parallel")
        regs = self._take(name)
        if not regs:
            return
        await asyncio.gather(*(reg.listener(*args) for reg in regs))

    async def serial(self, name: str, *args: Any) -> list[Any]:
        """串行：按注册顺序依次 await，收集每个的返回值。"""
        self._check(name, "serial")
        results = []
        for reg in self._take(name):
            results.append(await reg.listener(*args))
        return results

    async def bail(self, name: str, *args: Any) -> Any | None:
        """首个非 None 的返回值胜出，后面的监听器不再跑。"""
        self._check(name, "bail")
        for reg in self._take(name):
            value = await reg.listener(*args)
            if value is not None:
                return value
        return None

    async def waterfall(
        self,
        name: str,
        *args: Any,
        terminal: Callable[..., Awaitable[Any]],
    ) -> Any:
        """环绕中间件。

        `terminal` 是真正干活的那个实现（通常是某个服务的方法）。
        监听器按注册顺序包在它外面：第一个注册的在最外层，
        所以策略插件可以用 `prepend=True` 抢到最外层位置。

        监听器签名是 `async def fn(*args, next)`：

        - 调 `next()` 继续往下，返回的就是下游的结果；
        - 调 `next(*new_args)` 换掉参数再往下——改写请求就靠这个；
        - 不调 `next()` 直接 return，就是短路：下游和 terminal 都不跑。
        """
        self._check(name, "waterfall")
        chain: list[Listener] = [*self.listeners(name), terminal]

        async def step(index: int, call_args: tuple[Any, ...]) -> Any:
            if index >= len(chain):
                raise DuGentXError(f"waterfall {name!r} 走到了尽头却没有 terminal")

            handler = chain[index]

            async def nxt(*replacement: Any) -> Any:
                return await step(index + 1, replacement or call_args)

            if handler is terminal:
                return await handler(*call_args)
            return await handler(*call_args, nxt)

        return await step(0, args)


@dataclass(slots=True)
class EventSpec:
    """一个事件的声明。派发方式是公共契约的一部分，所以要写下来。"""

    name: str
    mode: str
    summary: str
    payload: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.mode not in ALLOWED_MODES:
            raise DuGentXError(f"未知的派发方式：{self.mode}")
