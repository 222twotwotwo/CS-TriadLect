"""ask_human 工具 —— human 缝的第二个消费者。

第一个消费者是审批（permissions 缝）。这个工具证明那条缝不是为审批
量身定做的：任何「模型需要问人一句」的场景走的都是同一条通道，
而通道那一头是终端还是 TUI，这里同样一无所知。

为什么要给模型一个「问人」的工具？因为没有它的时候，模型遇到模糊需求
只有两条路：猜，或者停下来说「我需要更多信息」。前者是慢性的错，
后者在交互式场景里是浪费——人在那儿坐着呢。

标签是 `read`：问一句话不改变世界，不该触发写操作的确认弹窗。
但它**会阻塞**，所以工具描述里必须说清楚这一点——模型看到
「会一直等到有人回答」和看到「立刻返回」是两种完全不同的决策依据。
"""

from __future__ import annotations

from typing import Any

from dugentx.kernel.context import Context
from dugentx.kernel.plugin import define_plugin
from dugentx.seams.human import Question
from dugentx.seams.tools import LABEL_READ, tool_from_function


async def ask_human(ctx: Context, question: str, context: str = "") -> str:
    """向人问一个问题，并等到有人回答。用于需求模糊、需要做选择的时候。

    Args:
        question: 要问的话，尽量具体，一次只问一件事
        context: 为什么问、你已经知道什么，帮人快速判断
    """
    channel = ctx.get("human")
    if channel is None:
        return "这台机器上没有可用的人机通道，问不到人；请自己判断或说明你无法确定。"

    if not channel.interactive():
        return "现在没有人能回答（不是交互式终端）。请基于已有信息继续，并在结论里说明你的假设。"

    ctx.events.emit("human/asked", question, getattr(channel, "name", "?"))
    answer = await channel.ask(Question(prompt=question, detail=context, title="ask_human"))
    ctx.events.emit("human/answered", question, answer)

    if answer.cancelled:
        return "对方取消了这次提问。请基于已有信息继续，并说明你做的假设。"
    if not answer.text.strip():
        return "对方没有给出内容。请换一种问法，或基于已有信息继续。"
    return answer.text


@define_plugin(
    "ask-tools",
    inject=("tools",),
    description="给模型一个向人提问的工具，走 human 缝",
)
def create(ctx: Context, config: dict[str, Any] | None = None) -> None:
    """注册 `ask_human`。它不提供新服务，只是给模型多一只手。"""
    config = config or {}
    ctx.effect(
        lambda inner: inner.service("tools").register(
            tool_from_function(
                ask_human,
                name=str(config.get("tool_name", "ask_human")),
                labels=frozenset({LABEL_READ}),
            )
        ),
        label="register:ask_human",
    )
