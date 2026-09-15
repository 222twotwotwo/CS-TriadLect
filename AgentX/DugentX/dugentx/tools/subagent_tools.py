"""delegate_task 工具 —— 把一件事整个交给子 Agent。

这个工具的标签是 `write`，不是 `read`。理由不是技术性的，是诚实的：
子 Agent 会在你的名义下动这个世界——它能写文件、能跑命令。权限策略要知道
「这次调用可能会写东西」，否则一次委托就绕开了所有写操作的确认。

模型拿到的是 `SubagentResult.render()`：结论，加上「谁用了多少步」这一行。
子 Agent 的过程不进父会话，所以那一行不是装饰——它是在没有过程的情况下，
唯一能让人判断这个结论值不值得信的东西：三步的结论和三步的猜测，
读起来是一样的。
"""

from __future__ import annotations

from typing import Any

from dugentx.kernel.context import Context
from dugentx.kernel.effect import Disposer
from dugentx.kernel.errors import PluginError
from dugentx.kernel.plugin import define_plugin
from dugentx.seams.subagent import SubagentRequest
from dugentx.seams.tools import LABEL_WRITE, tool_from_function


async def delegate_task(
    ctx: Context,
    prompt: str,
    label: str = "sub",
    max_steps: int = 16,
) -> str:
    """把一件事交给一个独立会话的助手去做，只把它交回来的结论带回上下文。

    适合委托的是那种「要读很多、结论很短」的活：把某个模块的调用链理清楚、
    把一批文件里的用法找齐、审查一段 diff。子助手读过的原文不会进入你的上下文，
    所以你拿到的只有结论——如果结论不够，就带着更具体的问题再委托一次。

    Args:
        prompt: 交给子助手的任务，写清楚目标和判定标准，它看不到你们的对话
        label: 这次委托的名字，只用于日志和结果抬头
        max_steps: 子助手最多走几步；这是熔断，不是完成条件
    """
    request = SubagentRequest(prompt=prompt, label=label, max_steps=max_steps)
    result = await ctx.service("subagents").spawn(ctx, request)
    return result.render()


def register(ctx: Context) -> Disposer:
    """把 `delegate_task` 注册进工具表，返回撤销它的 disposer。"""
    return ctx.service("tools").register(
        tool_from_function(delegate_task, labels=frozenset({LABEL_WRITE}))
    )


@define_plugin(
    "subagent-tools",
    inject=("tools", "subagents"),
    description="delegate_task：把一段「读得多、结论短」的活外包出去",
)
def create(ctx: Context, config: dict[str, Any]) -> None:
    """注册委托工具。

    依赖只要 `tools` 和 `subagents`：这个工具不自己造子 Agent，也不碰父会话的
    日志——委托的结果怎么回填是 agent 循环的事，它只负责发起。
    """
    if config:
        raise PluginError(
            f"subagent-tools 没有可配的项，却收到了 {sorted(config)}；"
            f"子 Agent 的默认模型和步数配在 subagent 插件上"
        )
    ctx.effect(register, label="tool:delegate_task")
