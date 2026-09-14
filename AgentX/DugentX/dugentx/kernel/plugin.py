"""插件。

一个插件就是「一个名字 + 它要用的服务 + 一段 apply」。

`inject` 是**装载顺序的唯一来源**：插件声明自己要哪些 `ctx.<key>` 服务，
装载器据此排出顺序。手写启动顺序是 dsh 明确反对的做法，
因为顺序一旦写死在 boot 代码里，插件就不能被拔下来了。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# 这里**故意**不 import Context：
# context.py 的 mount() 需要一个 Plugin，plugin.py 的类型别名只需要 Callable。
# 互相 import 会成环，而这一层真的没有别的依赖需求——
# 所以只保留结构化描述（签名写在 docstring 里）。
Apply = Callable[..., Any]
"""插件主体，签名是 `apply(ctx: Context, config: dict)`。可以是同步函数，也可以是 async 函数。

config 由装载器在 mount 时传进来（`Plugin.config`），不是插件自己去读配置——
插件不该知道配置是从 YAML 还是别处来的。
"""


@dataclass(slots=True)
class Plugin:
    """一个可装载单元。"""

    name: str
    apply: Apply
    inject: tuple[str, ...] = ()
    """装载前必须已经存在的服务键。装载器用它排顺序。"""

    provides: tuple[str, ...] = ()
    """这个插件会提供哪些服务键。配置校验用它——声明了却没人提供，装载时就报错。"""

    description: str = ""
    config: dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<Plugin {self.name} inject={list(self.inject)} provides={list(self.provides)}>"


def define_plugin(
    name: str,
    *,
    inject: tuple[str, ...] = (),
    provides: tuple[str, ...] = (),
    description: str = "",
) -> Callable[[Apply], Callable[..., Plugin]]:
    """把一个 `apply` 函数标记成插件工厂。

    用法：

    ```python
    @define_plugin("session", provides=("sessions",))
    def create(ctx: Context, config: dict) -> None:
        ...            # 装载时执行
    ```

    装饰后的对象是个**工厂**：调用 `create(config)` 得到一个 `Plugin`。
    统一走工厂而不是直接暴露 `Plugin`，是为了每个实例都拿到自己的
    配置副本——同一个插件被装载两次时不会互相踩。

    配置是**在 mount 时交给 apply 的第二参数**，不是插件自己去读：
    插件不需要知道配置来自 YAML、命令行还是测试里的一个字面量。
    """

    def decorate(apply: Apply) -> Callable[..., Plugin]:
        def factory(config: dict[str, Any] | None = None) -> Plugin:
            return Plugin(
                name=name,
                apply=apply,
                inject=inject,
                provides=provides,
                description=description or (apply.__doc__ or "").strip().split("\n")[0],
                config=dict(config or {}),
            )

        factory.__name__ = name
        factory.__dugentx_plugin__ = True  # type: ignore[attr-defined]
        return factory

    return decorate
