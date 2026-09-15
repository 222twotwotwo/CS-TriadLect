"""subagent 缝的默认 provider —— 在同一个进程里开一个子 Agent。

子 Agent 不是「再发一次模型请求」。它要有**自己的会话**，理由只有一条，
但它是整件事的全部理由：

> 子 Agent 存在的意义，就是把一段过程挪出主会话，好让主会话的上下文保持干净。

所以这里做两件看起来不对称、其实是一体两面的事：子 Agent 的每一步都记在
它自己的 `SessionLog` 里（**绝不抄回父会话**），只有结论以 `SubagentResult`
的形式回到父会话。把子 Agent 的过程抄进父日志，等于把外包出去的东西又搬回家，
父会话的上下文只会更大——那个机制一点好处都没有了。

子会话不会因此变成「没有记录」：它照样可以落到 `sessionStore` 上，
只是它不在父会话的上下文里。过程有记录，但不是我的上下文——
这正是「过程隔离、结论回归」那条界线。

子 Agent 跑完就从 `ctx.agents` 名单里摘掉。不摘的话，一个跑了几十次委托的
会话会留下几十个僵尸 agent，`ctx.agents.all()` 慢慢变成一个垃圾堆，
而运行期动态挂载、TUI 观测都靠这份名单。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from dugentx.kernel.context import Context
from dugentx.kernel.errors import PluginError
from dugentx.seams.agent import AgentConfig
from dugentx.seams.session import SessionLog
from dugentx.seams.subagent import SubagentRequest, SubagentResult


class InProcessSubagent:
    """`ctx.subagents` 的默认实现：子 Agent 是同一个进程里的另一个会话。"""

    CONFIG_KEYS = frozenset({"default_model", "max_steps", "tool_allow", "persist_sessions"})

    def __init__(
        self,
        *,
        default_model: str = "",
        max_steps: int = 16,
        tool_allow: Sequence[str] = (),
        persist_sessions: bool = True,
    ) -> None:
        if max_steps < 1:
            raise PluginError(
                f"max_steps 至少要是 1，收到 {max_steps}；"
                f"子 Agent 一步都不许走等于拒绝所有委托"
            )
        self.default_model = default_model
        self.max_steps = max_steps
        self.tool_allow = tuple(tool_allow)
        self.persist_sessions = persist_sessions

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> InProcessSubagent:
        """从配置行里长出 provider；未知键在这里报错，不留到第一次委托。"""
        unknown = set(config) - cls.CONFIG_KEYS
        if unknown:
            raise PluginError(
                f"subagent 配置里有不认识的键：{sorted(unknown)}；可用：{sorted(cls.CONFIG_KEYS)}"
            )
        return cls(
            default_model=str(config.get("default_model", "")),
            max_steps=int(config.get("max_steps", 16)),
            tool_allow=tuple(config.get("tool_allow") or ()),
            persist_sessions=bool(config.get("persist_sessions", True)),
        )

    # ---------------------------------------------------------------- 出发

    async def spawn(self, ctx: Context, request: SubagentRequest) -> SubagentResult:
        """跑一个子 Agent，只把结论带回来。

        子 Agent 的会话在这里新建，从 `session/start` 开始记。**父会话的日志
        一行都不会因为这次委托而变化**——这不是实现细节，这是这个缝的定义：
        委托的价值在于把过程挡在主会话之外。
        """
        agents = ctx.service("agents")
        session = SessionLog()
        config = AgentConfig(
            model=request.model or self.default_model,
            max_steps=self._steps_for(request),
            tool_allow=self._tools_for(request),
            label=request.label,
        )
        agent = agents.create(session=session, config=config, label=request.label)
        release = _releaser(agents, agent)

        ctx.events.emit("subagent/start", request)
        try:
            turn = await agent.send(request.prompt)
        except Exception as exc:  # 子 Agent 崩了不该把父会话一起带走
            ctx.events.emit("agent/error", f"subagent:{request.label}", exc)
            result = SubagentResult(
                output=f"（子 Agent 出错了：{type(exc).__name__}: {exc}）",
                session_id=session.session_id,
                stop_reason="error",
            )
        else:
            result = SubagentResult(
                output=turn.text,
                session_id=session.session_id,
                steps=turn.steps,
                usage=turn.usage,
                stop_reason=turn.stop_reason,
            )
        finally:
            agent.close()
            release()
        ctx.events.emit("subagent/end", result)

        # 子会话照样落盘：过程不进父上下文，但不是「没有记录」。
        store = ctx.get("sessionStore")
        if self.persist_sessions and store is not None:
            store.save(session)
        return result

    # ---------------------------------------------------------------- 收窄

    def _steps_for(self, request: SubagentRequest) -> int:
        """两边都收紧：provider 的 max_steps 是上限，请求可以更小，不能更大。

        「不能被请求放大」这一条重要：委托是主 Agent 自己发起的，如果它能
        随手把步数调到一千，那 provider 配的熔断就形同虚设。
        """
        wanted = request.max_steps if request.max_steps > 0 else self.max_steps
        return min(wanted, self.max_steps)

    def _tools_for(self, request: SubagentRequest) -> tuple[str, ...]:
        """工具白名单取交集。

        空元组的含义是「不限制」（见 `AgentConfig.tool_allow`），所以只在
        两边都有内容时才求交集；否则用有内容的那个。
        """
        if self.tool_allow and request.tool_allow:
            allowed = set(self.tool_allow)
            return tuple(name for name in request.tool_allow if name in allowed)
        return tuple(request.tool_allow) or self.tool_allow


def _releaser(agents: Any, agent: Any) -> Any:
    """拿到「把这个子 Agent 从名单里摘掉」的句柄。

    `AgentRegistry.register` 的返回值正好是这件事：它会把会话号对应的那一项
    弹出去。名单本身是软依赖（`agents` 只是被用到的服务，没规定实现），
    所以实现里没有这个方法时就不摘，而不是在这里炸掉。
    """
    register = getattr(agents, "register", None)
    if register is None:
        return lambda: None
    return register(agent)
