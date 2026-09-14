"""subagent 缝 —— 把一件事整个交出去。

子 Agent 不是「再开一次模型调用」。它要有**自己的会话**，因为：
子任务的过程是一条独立的轨迹，不该污染主会话的上下文；
但它的**结论**必须回到主会话，否则主 Agent 不知道发生了什么。

这条「过程隔离、结论回归」的界线就是子 Agent 的全部设计。
dsh 的 subagent 缝背后可以是从零起的子 agent、另一个产品的委托回合、
甚至跨进程的另一种 Agent——接口都一样。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from dugentx.kernel.context import Context
from dugentx.seams.messages import Usage


@dataclass(slots=True)
class SubagentRequest:
    """交给子 Agent 的任务。"""

    prompt: str
    label: str = "sub"
    model: str | None = None
    tool_allow: tuple[str, ...] = ()
    max_steps: int = 16

    def render(self) -> str:
        return f"[{self.label}] {self.prompt[:120]}"


@dataclass(slots=True)
class SubagentResult:
    """子 Agent 交回来的东西。**只有结论，没有过程。**"""

    output: str
    session_id: str
    steps: int = 0
    usage: Usage = field(default_factory=Usage)
    stop_reason: str = "completed"

    def render(self) -> str:
        head = f"（子 Agent {self.session_id} 用了 {self.steps} 步）"
        return f"{self.output}\n{head}"


@runtime_checkable
class SubagentProvider(Protocol):
    """`ctx.subagents`。"""

    async def spawn(self, ctx: Context, request: SubagentRequest) -> SubagentResult: ...
