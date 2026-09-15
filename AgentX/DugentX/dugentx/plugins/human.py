"""human 插件 —— 挂上「此刻人怎么跟 agent 说话」。

这一个插件就是「换人机通道」这个动作。装它得到 stdio 通道，
装 `dugentx.plugins.tui` 得到 TUI 通道——两者提供同一个服务键，
所以配置里只能有一个（一个键一个提供者），这正是我们要的：
一个进程里同时有两张嘴问同一个人，只会把问题问乱。

审批（permissions 缝）依赖的就是这个键。它不知道对面是终端还是 TUI，
它只知道「问一句、拿一个答案」。所以「加一个编码 TUI」没有改动审批一行代码。
"""

from __future__ import annotations

from typing import Any

from dugentx.kernel.context import Context
from dugentx.kernel.errors import PluginError
from dugentx.kernel.plugin import define_plugin
from dugentx.providers.human_stdio import StdioHuman


@define_plugin(
    "human",
    provides=("human",),
    description="标准输入输出上的人机通道：没有 TUI 时用这个",
)
def create(ctx: Context, config: dict[str, Any] | None = None) -> None:
    """装载 stdio 通道。配置只有一项：允不允许「一律允许」。"""
    config = config or {}
    unknown = set(config) - {"allow_always"}
    if unknown:
        raise PluginError(f"human 配置里有不认识的键：{sorted(unknown)}；可用：['allow_always']")

    channel = StdioHuman(allow_always=bool(config.get("allow_always", True)))
    ctx.provide("human", channel)
    ctx.effect(lambda _ctx: channel.close, label="human:close")
