"""subagent 插件 —— 把「委托」这件事变成一个服务。

子 Agent 的形态可以很不一样：同一个进程里的另一个会话、另一个进程、
另一种产品的委托回合。所以这里提供的不是一个类，而是一个**缝的默认实现**，
换掉它只需要在配置里换一行 `plugin:`——上层（`delegate_task` 工具、
未来的并行委托）一行都不用改。

配置只有一项是必须的：默认用哪个模型。`max_steps` 是熔断（子 Agent 走太久
通常说明这个任务该被拆开），`tool_allow` 是它的工具白名单——一个只负责
读代码的子 Agent 不该拿着写文件的权力。
"""

from __future__ import annotations

from typing import Any

from dugentx.kernel.context import Context
from dugentx.kernel.plugin import define_plugin
from dugentx.providers.subagent_inprocess import InProcessSubagent


@define_plugin(
    "subagent",
    inject=("agents",),
    provides=("subagents",),
    description="同进程的子 Agent：自己的会话，只把结论带回来",
)
def create(ctx: Context, config: dict[str, Any]) -> None:
    """装载子 Agent 的 provider。

    `inject` 里只要了 `agents`：子 Agent 是被 agent 名单创建出来的，
    而名单是共享的（父会话和子会话用的是同一份服务）。
    它**没有**要 `session`——子 Agent 新建自己的会话。
    要 `session` 的话就把父会话拿在手里了，那正是这个缝要避免的事。
    """
    ctx.provide("subagents", InProcessSubagent.from_config(config))
