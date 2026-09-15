"""agent-loop 缝的默认 provider —— 整份代码的心脏。

一个回合是这样走的：

```
turn/start
  把用户那句话追加进日志（从此它才「存在」）
  while True:
    step/start
    messages = derive_view(session)          ← 上下文只能来自日志
    messages = agent/pre-step (waterfall)    ← 压缩、注入、拒答都挂这里
    verify_projection(messages, session)     ← 模型可见 ⟺ 已记录
    request  = agent/request (waterfall)     ← 改请求的最后一道
    stream   = llm/stream (waterfall)        ← 包住整条模型流
    assistant/message                        ← 模型说了什么，先记下来
    step/end
    if 回复里没有工具请求: break              ← 这才是退出条件
    for call in 工具请求:
      outcome = tools.execute(call)          ← 四段管道（权限在这里）
      tool/result                            ← 结果回填，下一轮模型看得到
turn/end
```

三个刻意的选择，都值得单独说：

**退出条件是「这一轮没再请求工具」，不是「跑了 N 轮」。**
按轮数停会腰斩正常任务——一个需要读五个文件的任务，在第 8 步时
只是干到一半。`max_steps` 是熔断，不是完成判断。

**每一步都重新从日志投影上下文**，而不是在内存里维护一个 messages 列表。
这看起来慢，但它让「模型看到的」和「日志里记下的」不可能分叉——
压缩、恢复会话、子 agent、审计全都免费得到。

**异常不往上抛，而是变成 `agent/error` 事件和一次失败的 step。**
harness 崩掉比模型答错严重得多：前者丢掉整个会话，后者只是这一轮白跑。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from dugentx.kernel.context import Context
from dugentx.kernel.plugin import define_plugin
from dugentx.seams.agent import Agent, TurnResult
from dugentx.seams.llm import LlmRequest, assemble
from dugentx.seams.messages import Delta
from dugentx.seams.session import derive_view, verify_projection


async def _identity(value: Any) -> Any:
    return value


class BasicAgentLoop:
    """`ctx.agentLoop` 的默认实现。"""

    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx

    # ---------------------------------------------------------------- 一回合

    async def run_turn(self, agent: Agent, prompt: str) -> TurnResult:
        events = self.ctx.events
        session = agent.session
        result = TurnResult()

        session.append("turn/start", prompt=prompt)
        # 先记系统提示词，再记用户那句话：日志里顺序就是 [system, user, ...]，
        # 和真正发出去的请求一致，读日志的人不用在脑子里重排。
        self._sync_system_prompt(agent)
        session.append("user/message", content=prompt)
        events.emit("turn/start", prompt)

        try:
            result = await self._steps(agent, result)
        except Exception as exc:  # 循环自己出错也不许崩掉整个进程
            events.emit("agent/error", "agent-loop", exc)
            result.stop_reason = "error"
            result.text = f"（这一轮出错了：{type(exc).__name__}: {exc}）"
        finally:
            session.append(
                "turn/end",
                stop_reason=result.stop_reason,
                steps=result.steps,
                tool_calls=result.tool_calls,
                total_tokens=result.usage.total_tokens,
            )
            events.emit("turn/end", result)
        return result

    async def _steps(self, agent: Agent, result: TurnResult) -> TurnResult:
        events = self.ctx.events
        session = agent.session
        tools = self.ctx.service("tools")

        for step_index in range(agent.config.max_steps):
            events.emit("step/start", step_index)
            session.append("step/start", index=step_index)

            self._sync_system_prompt(agent)
            messages = derive_view(session)
            messages = await events.waterfall(
                "agent/pre-step", messages, terminal=_identity
            )
            # 这一行是「模型可见 ⟺ 已记录」的守门人。放在请求之前，
            # 而不是等出了事故再回头查——规则的价钱在执行点付才便宜。
            verify_projection(messages, session)

            request = LlmRequest(
                model=agent.config.model or self._default_model(),
                messages=messages,
                tools=agent.visible_tools(),
                temperature=agent.config.temperature,
                max_tokens=None,
                reasoning_effort=agent.config.reasoning_effort,
            )
            request = await events.waterfall("agent/request", request, terminal=_identity)

            turn = await self._call_model(agent, request)
            session.append(
                "assistant/message",
                content=turn.content,
                reasoning=turn.reasoning,
                tool_calls=[
                    {"id": c.id, "name": c.name, "arguments": c.arguments}
                    for c in turn.tool_calls
                ],
                prompt_tokens=turn.usage.prompt_tokens,
                completion_tokens=turn.usage.completion_tokens,
                total_tokens=turn.usage.total_tokens,
            )
            result.steps += 1
            result.usage = result.usage + turn.usage
            events.emit("step/end", step_index, len(turn.tool_calls))
            session.append("step/end", index=step_index, tool_calls=len(turn.tool_calls))

            if not turn.tool_calls:
                result.text = turn.content.strip()
                result.stop_reason = "completed"
                return result

            for call in turn.tool_calls:
                outcome = await tools.execute(call)
                # 回填的内容就是模型下一轮看到的内容，两者必须是同一个字符串。
                session.append(
                    "tool/result",
                    call_id=outcome.call_id,
                    name=outcome.name,
                    content=outcome.to_text(),
                    ok=outcome.ok,
                    blocked=outcome.blocked,
                )
                result.tool_calls += 1

        result.stop_reason = "max-steps"
        result.text = result.text or "（步数用完了，任务还没收口）"
        return result

    # ---------------------------------------------------------------- 模型

    async def _call_model(self, agent: Agent, request: LlmRequest) -> Any:
        """走 `llm/stream` 中间件拿到一条流，然后自己把它拼起来。"""
        events = self.ctx.events
        registry = self.ctx.service("llm")
        adapter = registry.adapter()

        async def terminal(req: LlmRequest) -> AsyncIterator[Delta]:
            return adapter.stream(req)

        stream = await events.waterfall("llm/stream", request, terminal=terminal)

        async def tapped() -> AsyncIterator[Delta]:
            async for delta in stream:
                events.emit("llm/chunk", delta)
                yield delta

        turn = await assemble(tapped())
        events.emit("llm/usage", turn.usage)
        return turn

    def _default_model(self) -> str:
        registry = self.ctx.get("llm")
        return str(getattr(registry, "default_model", "") or "")

    def _sync_system_prompt(self, agent: Agent) -> None:
        """把当前系统提示词记进日志——**变了才记一条新的**。

        系统提示词不是常量：工具清单、技能目录、工作区路径都在里面。
        agent 在会话中途挂上一个插件，提示词就会变；那一刻必须留痕，
        否则重放这个会话时，复现不出当时真正发出去的请求。

        比对而不是每次都写，是因为提示词在绝大多数 step 里是不变的——
        每个 step 都追加一条，日志会被稀释成噪声。
        """
        prompt = self.ctx.get("prompt")
        if prompt is None:
            return
        text = prompt.assemble(agent.ctx).strip()
        if not text:
            return
        previous = agent.session.last("system/message")
        if previous is not None and previous.data.get("content") == text:
            return
        agent.session.append(
            "system/message",
            content=text,
            reason="series" if previous is None else "change",
        )


@define_plugin(
    "agentLoop",
    inject=("llm", "tools", "session"),
    provides=("agentLoop",),
    description="默认的 agent 循环：一次模型请求 + 它请求的工具，直到模型不再要工具",
)
def create(ctx: Context, config: dict[str, Any] | None = None) -> None:
    """装载默认循环。

    循环本身也是服务（`ctx.agentLoop`），所以它可以被整个换掉——
    想做并行工具调用、想做 PTC、想做人在环里的暂停恢复，
    都是换一个实现了 `AgentDriver` 的插件，而不是来这里改 if。
    """
    ctx.provide("agentLoop", BasicAgentLoop(ctx))
