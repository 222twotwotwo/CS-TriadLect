"""agent 缝 —— 一个会话的载体，以及驱动它的那个循环。

词汇照 dsh：

- **step（步）**：一次模型请求，加上它请求的那些工具。这是最小的推进单位。
- **turn（回合）**：零个或多个 step。从「拿到一条用户输入」开始，
  到「不再欠模型任何东西」为止。为什么要有 turn 这个概念？
  因为「一次问答」和「一次请求」不是一回事——模型可能要工具、
  拿到结果、再要一次，这些都属于**同一个回合**。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from dugentx.kernel.context import Context
from dugentx.kernel.effect import Disposer
from dugentx.seams.messages import Usage
from dugentx.seams.session import SessionLog


@dataclass(slots=True)
class AgentConfig:
    """一个 agent 的配置。回合之间可变（比如运行期换了模型）。"""

    model: str = ""
    max_steps: int = 24
    """一个 turn 内最多走几个 step。这是**熔断**，不是退出条件——
    退出条件是「模型这一轮没再请求工具」。数够了就停会腰斩正常任务。"""

    temperature: float | None = None
    reasoning_effort: str | None = None
    tool_allow: tuple[str, ...] = ()
    """非空时视为白名单：只有列出的工具对模型可见。"""

    tool_deny: tuple[str, ...] = ()
    prompt_extras: dict[str, str] = field(default_factory=dict)
    context_budget_tokens: int = 60_000
    """超过这个量就触发 compaction 缝。"""

    label: str = "main"


@dataclass(slots=True)
class TurnResult:
    """一个回合的结果。"""

    text: str = ""
    steps: int = 0
    tool_calls: int = 0
    usage: Usage = field(default_factory=Usage)
    stop_reason: str = "completed"
    """completed / max-steps / empty / error"""

    @property
    def ok(self) -> bool:
        return self.stop_reason == "completed"


class AgentDriver(Protocol):
    """`ctx.agentLoop` —— 循环本身也是一个可替换的服务。"""

    async def run_turn(self, agent: Agent, prompt: str) -> TurnResult: ...


class Agent:
    """一次会话。持有日志、配置和上下文；具体怎么跑由 driver 决定。"""

    def __init__(
        self,
        ctx: Context,
        session: SessionLog,
        config: AgentConfig,
    ) -> None:
        self.ctx = ctx
        self.session = session
        self.config = config
        self._closed = False

    # ---------------------------------------------------------------- 回合

    async def send(self, prompt: str) -> TurnResult:
        """跑一个回合：喂进一条用户输入，拿回最终答复。"""
        if self._closed:
            raise RuntimeError("这个会话已经结束了")
        driver: AgentDriver = self.ctx.service("agentLoop")
        return await driver.run_turn(self, prompt)

    def inject(self, text: str) -> None:
        """把一段上下文塞给模型，但不当作「用户说的话」。

        注入的东西同样要走日志（模型可见 ⟺ 已记录），
        否则恢复会话时模型看到的历史和当时不一样。
        """
        self.session.append("context/injected", content=text)

    # ---------------------------------------------------------------- 工具

    def visible_tools(self) -> list[dict[str, Any]]:
        """这一轮模型能看到哪些工具。白名单/黑名单在这里生效。"""
        registry = self.ctx.service("tools")
        specs: list[dict[str, Any]] = []
        for tool in registry.all():
            if self.config.tool_allow and tool.name not in self.config.tool_allow:
                continue
            if tool.name in self.config.tool_deny:
                continue
            specs.append(tool.spec())
        return specs

    # ---------------------------------------------------------------- 生命周期

    def close(self) -> None:
        self._closed = True
        self.session.append("session/end")


class AgentRegistry:
    """`ctx.agents` —— 活着的 agent 名单。

    子 agent、运行期挂载的插件、外部的观测界面都通过它找到当前会话。
    """

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx
        self._agents: dict[str, Agent] = {}

    def create(
        self,
        *,
        session: SessionLog | None = None,
        config: AgentConfig | None = None,
        label: str = "main",
    ) -> Agent:
        # 注意是 `is None` 而不是 `or`：SessionLog 定义了 __len__，
        # 于是一个**空日志是假值**，`session or SessionLog()` 会悄悄换成一个新对象。
        # 这种 bug 的症状是「日志明明在写，文件却一直是空的」，极难查。
        if session is None:
            session = SessionLog()
        config = config or AgentConfig()
        config.label = label
        agent = Agent(self._ctx, session, config)
        self._agents[agent.session.session_id] = agent
        session.append("session/start", label=label, model=config.model)
        return agent

    def get(self, session_id: str) -> Agent | None:
        return self._agents.get(session_id)

    def all(self) -> list[Agent]:
        return list(self._agents.values())

    def register(self, agent: Agent) -> Disposer:
        self._agents[agent.session.session_id] = agent
        return lambda: self._agents.pop(agent.session.session_id, None)


HookFn = Callable[[Agent], Awaitable[None] | None]
