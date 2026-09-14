"""循环测试。

这是最重要的一组测试：它钉住的不是「代码能跑」，而是循环的几条设计承诺：

- 退出条件是「这一轮没再请求工具」，不是轮数；
- 工具结果作为一条消息回填，模型下一轮看得到；
- 工具失败和被拦截都是**结果**，不是异常；
- 「模型可见 ⟺ 已记录」在每个 step 之前真的被检查，而且能被违反。

全程离线：模型用一个脚本化的假适配器，不联网、不需要 key。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from dugentx.events import EVENT_MODES
from dugentx.kernel.context import Context
from dugentx.kernel.events import EventBus
from dugentx.providers.agent_loop_basic import create as agent_loop_plugin
from dugentx.seams.agent import AgentConfig, AgentRegistry
from dugentx.seams.llm import Delta, LlmRegistry, LlmRequest
from dugentx.seams.messages import Message
from dugentx.seams.session import SessionLog, derive_messages, verify_projection
from dugentx.seams.tools import LABEL_READ, LABEL_WRITE, Tool, ToolRegistry

# ------------------------------------------------------------------ 假模型


class ScriptedAdapter:
    """按脚本一轮一轮回话的假模型。

    它实现了真正的 `LlmAdapter` 协议，所以循环完全不知道自己在跟一个假的说话——
    这正是「LLM 是一个缝」的用处：测试不需要打桩框架，只需要另一个 provider。
    """

    name = "scripted"

    def __init__(self, turns: list[list[Delta]]) -> None:
        self.turns = list(turns)
        self.requests: list[LlmRequest] = []

    async def stream(self, request: LlmRequest) -> AsyncIterator[Delta]:
        self.requests.append(request)
        turn = self.turns.pop(0) if self.turns else [Delta(text="（脚本用完了）")]
        for delta in turn:
            yield delta


def text(body: str, *, chunk: int = 0) -> list[Delta]:
    if not chunk:
        return [Delta(text=body)]
    return [Delta(text=body[i : i + chunk]) for i in range(0, len(body), chunk)]


def calls(*pairs: tuple[str, str, dict[str, Any]]) -> list[Delta]:
    """构造一轮「请求工具」的回复。"""
    out: list[Delta] = []
    for call_id, name, args in pairs:
        out.append(Delta(tool_call_id=call_id, tool_name=name, arguments_delta="{", text=""))
        import json

        out.append(Delta(tool_call_id=call_id, arguments_delta=json.dumps(args)[1:]))
    for delta in out:
        if not delta.arguments_delta and not delta.text:
            delta.text = ""
    return out


# ------------------------------------------------------------------ 装配


class Harness:
    """把一个最小可用的运行时手工装起来。

    注意这里**没有配置文件、没有 loader**：循环测试不该依赖 YAML。
    装配是显式的，每一步都看得见。
    """

    def __init__(self, turns: list[list[Delta]], *, max_steps: int = 8) -> None:
        self.ctx = Context("test", events=EventBus(EVENT_MODES))
        self.adapter = ScriptedAdapter(turns)
        registry = LlmRegistry()
        registry.register(self.adapter)
        self.ctx.provide("llm", registry)
        self.tools = ToolRegistry(self.ctx)
        self.ctx.provide("tools", self.tools)
        self.log = SessionLog()
        self.ctx.provide("session", self.log)

    async def start(self) -> Harness:
        await self.ctx.mount(agent_loop_plugin({}))
        agents = AgentRegistry(self.ctx)
        self.ctx.provide("agents", agents)
        self.agent = agents.create(
            session=self.log, config=AgentConfig(model="scripted", max_steps=8)
        )
        return self

    def add_tool(self, name: str, handler, *, labels=frozenset({LABEL_READ})):  # type: ignore[no-untyped-def]
        self.tools.register(
            Tool(
                name=name,
                description=f"{name} 工具",
                parameters={"type": "object", "properties": {}, "required": []},
                handler=handler,
                labels=labels,
            )
        )


# ------------------------------------------------------------------ 测试


def test_only_the_latest_system_message_is_projected_and_it_comes_first() -> None:
    """一次请求里只有一个系统提示词。

    它会变（工具清单变了它就变），但历史里不该堆一排系统消息；
    而且它必须排在最前面——排在用户消息后面的系统消息，
    很多 provider 会直接拒绝整个请求。
    """
    log = SessionLog()
    log.append("session/start")
    log.append("user/message", content="第一句")
    log.append("system/message", content="提示词 v1")
    log.append("system/message", content="提示词 v2")

    messages = derive_messages(log)

    assert [m.role for m in messages] == ["system", "user"]
    assert messages[0].content == "提示词 v2"
    assert messages[1].content == "第一句"


async def test_the_loop_records_the_system_prompt_before_the_user_message() -> None:
    """日志里的顺序要和真正发出去的请求一致，读日志的人不用在脑子里重排。"""
    from dugentx.seams.prompt import PromptRegistry, static

    h = await Harness([text("好")]).start()
    prompt = PromptRegistry()
    prompt.section("persona", static("你是 DugentX。"), order=10)
    h.ctx.provide("prompt", prompt)

    await h.agent.send("你好")

    kinds = [e.kind for e in h.log]
    assert kinds.index("system/message") < kinds.index("user/message")
    assert h.adapter.requests[0].messages[0].role == "system"


async def test_the_system_prompt_is_recorded_again_only_when_it_changes() -> None:
    """每一轮都记一条会把日志稀释成噪声；只在变了时留痕。"""
    from dugentx.seams.prompt import PromptRegistry, static

    h = await Harness([text("一"), text("二")]).start()
    prompt = PromptRegistry()
    prompt.section("persona", static("你是 DugentX。"), order=10)
    h.ctx.provide("prompt", prompt)

    await h.agent.send("第一轮")
    await h.agent.send("第二轮")

    assert h.log.count("system/message") == 1


async def test_an_empty_session_log_is_never_silently_replaced() -> None:
    """回归测试：`SessionLog` 定义了 `__len__`，所以**空日志是假值**。

    一个 `session or SessionLog()` 就会把调用方传进来的日志换成新对象，
    症状是「日志明明在写，磁盘上一直空着」。这条测试把它钉住。
    """
    h = await Harness([text("好")]).start()
    assert h.agent.session is h.log
    await h.agent.send("你好")
    assert len(h.log) > 0


async def test_one_step_with_no_tool_calls_completes() -> None:
    h = await Harness([text("你好，我是模型")]).start()
    result = await h.agent.send("打个招呼")

    assert result.ok
    assert result.text == "你好，我是模型"
    assert result.steps == 1
    assert result.tool_calls == 0
    assert [e.kind for e in h.log] == [
        "session/start",
        "turn/start",
        "user/message",
        "step/start",
        "assistant/message",
        "step/end",
        "turn/end",
    ]


async def test_streamed_text_is_stitched() -> None:
    """delta 是增量，拼装是 harness 的事——按一个字符一个 chunk 喂进去。"""
    h = await Harness([text("拼起来是这样", chunk=1)]).start()
    result = await h.agent.send("随便说点什么")
    assert result.text == "拼起来是这样"


async def test_tool_call_round_trip_and_exit_by_absence_of_tools() -> None:
    """一个完整的回合：请求工具 → 执行 → 回填 → 再问 → 没有工具了 → 结束。"""
    h = await Harness(
        [
            calls(("c1", "read_file", {"path": "a.txt"})),
            text("文件里写着 hello"),
        ]
    ).start()

    seen: list[str] = []

    async def read_file(path: str) -> str:
        seen.append(path)
        return "hello"

    h.add_tool("read_file", read_file)
    result = await h.agent.send("读一下 a.txt")

    assert seen == ["a.txt"]
    assert result.steps == 2
    assert result.tool_calls == 1
    assert result.text == "文件里写着 hello"
    assert result.ok

    kinds = [e.kind for e in h.log]
    assert kinds.count("step/start") == 2
    assert kinds.index("tool/result") > kinds.index("assistant/message")


async def test_the_model_sees_the_tool_result_on_the_next_step() -> None:
    """回填的内容和模型看到的内容必须是同一个字符串，否则这条链就断了。"""
    h = await Harness(
        [
            calls(("c1", "read_file", {"path": "a.txt"})),
            text("读到了"),
        ]
    ).start()

    async def read_file(path: str) -> str:
        return "内容：42"

    h.add_tool("read_file", read_file)
    await h.agent.send("读一下")

    second_request = h.adapter.requests[1]
    tool_messages = [m for m in second_request.messages if m.role == "tool"]
    assert len(tool_messages) == 1
    assert tool_messages[0].content == "内容：42"
    assert tool_messages[0].tool_call_id == "c1"


async def test_a_failing_tool_is_a_result_not_an_exception() -> None:
    """工具坏了不该打断会话——回一条错误消息，让模型自己想办法。"""
    h = await Harness(
        [
            calls(("c1", "read_file", {"path": "missing.txt"})),
            text("那个文件不存在，我们换个办法"),
        ]
    ).start()

    async def read_file(path: str) -> str:
        raise RuntimeError("文件不存在")

    h.add_tool("read_file", read_file)
    result = await h.agent.send("读一下")

    assert result.ok
    assert result.text == "那个文件不存在，我们换个办法"
    tool_events = h.log.of_kind("tool/result")
    assert len(tool_events) == 1
    assert tool_events[0].data["ok"] is False
    assert "RuntimeError" in str(tool_events[0].data["content"])


async def test_a_blocked_tool_is_reported_as_blocked() -> None:
    """被权限拦下来是一种**结果**，而且要在日志里看得出「是拦的，不是坏的」。"""
    from dugentx.kernel.errors import ToolBlocked

    h = await Harness([calls(("c1", "write_file", {"path": "x"})), text("好，那我不写了")]).start()

    async def write_file(path: str) -> str:
        raise ToolBlocked("写文件需要确认，而这次没有人点头")

    h.add_tool("write_file", write_file, labels=frozenset({LABEL_WRITE}))
    await h.agent.send("写个文件")

    event = h.log.of_kind("tool/result")[0]
    assert event.data["blocked"] is True
    assert "需要确认" in str(event.data["content"])


async def test_max_steps_is_a_fuse_not_a_finish_line() -> None:
    """模型一直要工具、永远不停时，循环要能停下来，并说清是怎么停的。"""
    always = [calls(("c1", "ping", {})) for _ in range(20)]
    h = await Harness(always).start()
    h.agent.config.max_steps = 3

    async def ping() -> str:
        return "pong"

    h.add_tool("ping", ping)
    result = await h.agent.send("一直敲")

    assert result.stop_reason == "max-steps"
    assert result.steps == 3


async def test_projection_invariant_holds_at_the_end_of_a_turn() -> None:
    h = await Harness([calls(("c1", "read_file", {"path": "a"})), text("好了")]).start()

    async def read_file(path: str) -> str:
        return "内容"

    h.add_tool("read_file", read_file)
    await h.agent.send("读")

    from dugentx.seams.session import derive_view

    verify_projection(derive_view(h.log), h.log)


async def test_the_invariant_actually_fires_when_someone_smuggles_a_message() -> None:
    """证明那道守门人不是装饰：往请求里塞一条没记录的消息，必须当场炸。"""
    h = await Harness([text("嗯")]).start()

    async def smuggler(messages, nxt):  # type: ignore[no-untyped-def]
        return await nxt([*messages, Message(role="user", content="我偷偷加的")])

    h.ctx.events.on("agent/pre-step", smuggler)

    result = await h.agent.send("你好")
    assert result.stop_reason == "error"
    assert "模型可见 ⟺ 已记录" in result.text


async def test_a_pre_step_listener_can_refuse_the_turn() -> None:
    """`agent/pre-step` 是 waterfall：不调 next() 直接返回，就是拒答。"""
    h = await Harness([text("不该被问到")]).start()

    async def gate(messages, nxt):  # type: ignore[no-untyped-def]
        return []

    h.ctx.events.on("agent/pre-step", gate)
    result = await h.agent.send("你好")

    assert result.stop_reason == "error"  # 空消息列表过不了断言
    assert len(h.adapter.requests) == 0


async def test_a_request_listener_can_rewrite_the_request() -> None:
    """改写请求也是 waterfall 的用法：next(换过的对象)。"""
    h = await Harness([text("收到")]).start()

    async def bump(request, nxt):  # type: ignore[no-untyped-def]
        request.temperature = 0.9
        return await nxt(request)

    h.ctx.events.on("agent/request", bump)
    await h.agent.send("你好")

    assert h.adapter.requests[0].temperature == 0.9


async def test_an_llm_stream_listener_can_wrap_the_stream() -> None:
    """`llm/stream` 包住整条流——这是做本地缓存、审计、脱敏的位置。"""
    h = await Harness([text("原始内容")]).start()
    seen: list[str] = []

    async def wrapper(request, nxt):  # type: ignore[no-untyped-def]
        async def tapped() -> AsyncIterator[Delta]:
            async for delta in await nxt():
                seen.append(delta.text)
                yield delta

        return tapped()

    h.ctx.events.on("llm/stream", wrapper)
    result = await h.agent.send("你好")

    assert result.text == "原始内容"
    assert seen == ["原始内容"]


async def test_injected_context_is_model_visible_and_logged() -> None:
    """`inject()` 塞进去的东西也是模型可见的，所以它同样必须进日志。"""
    h = await Harness([text("好")]).start()
    h.agent.inject("今天是星期二")

    await h.agent.send("今天是几号")

    assert "context/injected" in [e.kind for e in h.log]
    assert any("星期二" in m.content for m in h.adapter.requests[0].messages)


async def test_empty_assistant_reply_still_closes_the_turn() -> None:
    h = await Harness([[]]).start()
    result = await h.agent.send("?")
    assert result.text == ""
    assert h.log.last("turn/end") is not None


@pytest.mark.parametrize("bad_arguments", ['{"path": ', "not json at all", ""])
async def test_malformed_tool_arguments_do_not_crash_the_loop(bad_arguments: str) -> None:
    """流式拼出来的 JSON 经常不完整。这里不抛异常，让工具去报「缺参数」。"""
    from dugentx.seams.llm import parse_arguments

    assert parse_arguments(bad_arguments) == {}


async def test_derive_messages_matches_the_log_after_a_tool_round() -> None:
    h = await Harness([calls(("c1", "read_file", {"path": "a"})), text("完")]).start()

    async def read_file(path: str) -> str:
        return "x"

    h.add_tool("read_file", read_file)
    await h.agent.send("读")

    messages = derive_messages(h.log)
    roles = [m.role for m in messages]
    assert roles == ["user", "assistant", "tool", "assistant"]
    assert messages[1].tool_calls[0].name == "read_file"
