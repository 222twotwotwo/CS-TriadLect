"""压缩的测试：三种机制，和两条不许破的不变量。

这些测试全是离线的：一个不联网的 stub adapter 顶替模型，机械摘录那条路
更是一条网络调用都没有。压缩是「跑久了必然发生」的那件事，如果它的测试
需要 API key，那它实际上不会被测到。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from dugentx.events import EVENT_MODES
from dugentx.kernel.context import Context
from dugentx.kernel.errors import DuGentXError
from dugentx.kernel.events import EventBus
from dugentx.plugins.compaction import create as create_compaction
from dugentx.plugins.compaction import encode_drop
from dugentx.providers.compaction_basic import BasicCompactor, assert_tool_pairing
from dugentx.seams.compaction import messages_tokens
from dugentx.seams.llm import Delta, LlmRegistry
from dugentx.seams.messages import Message
from dugentx.seams.session import SessionLog, derive_view, verify_projection


def _ctx(**services: Any) -> Context:
    ctx = Context("test", events=EventBus(EVENT_MODES))
    for key, value in services.items():
        ctx.provide(key, value)
    return ctx


def _noise(lines: int) -> str:
    """一次「读了四百行文件」的工具结果：窗口里最典型的那种大东西。"""
    return "\n".join(
        f"{index:>4}    if mode == 'x{index}': return {index}" for index in range(lines)
    )


def _conversation() -> SessionLog:
    """一段典型的、已经长起来的会话：两次读文件，中间夹着人说的话。"""
    log = SessionLog("s-main")
    log.append("session/start", label="main", model="m")
    log.append("user/message", content="改一下 config 的加载顺序。不要动生产配置。")
    log.append(
        "assistant/message",
        content="先读一下这个文件。",
        tool_calls=[{"id": "c1", "name": "read_file", "arguments": {"path": "config.py"}}],
    )
    log.append("tool/result", call_id="c1", name="read_file", content=_noise(400))
    log.append("assistant/message", content="顺序反了：先读 yaml 再加载 .env。")
    log.append("user/message", content="那就改过来。")
    log.append(
        "assistant/message",
        content="",
        tool_calls=[{"id": "c2", "name": "read_file", "arguments": {"path": "loader.py"}}],
    )
    log.append("tool/result", call_id="c2", name="read_file", content=_noise(400))
    log.append("assistant/message", content="改好了。")
    log.append("user/message", content="跑一下测试。")
    return log


async def _identity(value: Any) -> Any:
    return value


class _StubAdapter:
    """一个不联网的模型：它只回一句固定的话，并把自己收到的请求记下来。"""

    name = "stub"

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.requests: list[Any] = []

    async def stream(self, request: Any) -> AsyncIterator[Delta]:
        self.requests.append(request)
        yield Delta(text=self.reply)


# ------------------------------------------------------------------ 剪枝


async def test_prune_shrinks_the_big_tool_result_and_keeps_its_call() -> None:
    """剪枝对准工具结果：正文出去，调用和开头留下。"""
    log = _conversation()
    compactor = BasicCompactor(keep_recent_tool_lines=6, keep_recent_messages=2)
    result = await compactor.compact(_ctx(), derive_view(log), budget_tokens=1200)

    assert result.changed and result.dropped > 0
    assert result.after_tokens < result.before_tokens
    assert "剪枝" in result.note

    text = "\n".join(message.content for message in result.messages)
    # 四百行的工具结果不再原样留在窗口里。
    assert "x399" not in text
    # 但它的调用没丢：工具名、参数、结果开头，都在摘要里。
    summary = result.messages[0].content
    assert "read_file" in summary
    assert "config.py" in summary
    assert "if mode == 'x0'" in summary


# ------------------------------------------------------------------ 摘要


async def test_summary_falls_back_to_extractive_when_no_adapter_is_configured() -> None:
    """没有模型就退到机械摘录，而不是让整个 step 失败。"""
    log = _conversation()
    result = await BasicCompactor(keep_recent_messages=2).compact(
        _ctx(), derive_view(log), budget_tokens=1200
    )

    summary = result.messages[0].content
    assert "机械摘录" in summary  # 明确写出自己是什么
    assert "改一下 config 的加载顺序" in summary
    # 摘录就是摘录：一条消息只留开头。这正是它必须被标出来的原因——
    # 它看着像摘要，但只看得到第一条，看不到后面那句「不要动生产配置」。
    assert "不要动生产配置" not in summary
    assert result.summarized == result.dropped
    assert "没有可用模型" in result.note


async def test_summarize_asks_the_model_to_keep_decisions_paths_and_errors() -> None:
    """有模型时走模型，并且提示词必须点名叫它保住那三样东西。"""
    log = _conversation()
    adapter = _StubAdapter("决定：把 yaml 的加载提到 .env 之前。涉及 config.py 与 loader.py。")
    registry = LlmRegistry()
    registry.register(adapter, default=True)

    result = await BasicCompactor(keep_recent_messages=2).compact(
        _ctx(llm=registry), derive_view(log), budget_tokens=1200, model="stub-model"
    )

    assert result.messages[0].content == adapter.reply
    assert "模型写的" in result.note

    sent = adapter.requests[0]
    assert sent.model == "stub-model"
    system_prompt = sent.messages[0].content
    assert "决定" in system_prompt and "文件路径" in system_prompt and "错误" in system_prompt
    # 摘要模型拿到的材料里带着工具调用的身份，不是光秃秃的结果。
    assert "read_file" in sent.messages[1].content


# ------------------------------------------------------------------ 截断


async def test_truncation_never_drops_the_system_prefix() -> None:
    """system 前缀不参与压缩：丢掉它模型就换了一个人在说话。"""
    system = Message(role="system", content="你是 DugentX 的助手。有一条硬规矩：不许动生产配置。")
    tail = [
        Message(role="user", content="把这段代码再读一遍，然后告诉我它到底做了什么。" * 3)
        for _ in range(12)
    ]
    result = await BasicCompactor(keep_recent_messages=1).compact(
        _ctx(), [system, *tail], budget_tokens=200
    )

    assert result.messages[0] == system
    assert "不许动生产配置" in result.messages[0].content
    assert sum(1 for message in result.messages if message.role == "system") == 1
    assert result.dropped > 0
    assert result.after_tokens < result.before_tokens


async def test_a_system_message_in_the_middle_is_refused() -> None:
    """夹在中间的 system 消息是拼错了，压缩拒绝工作而不是碰运气。"""
    messages = [
        Message(role="user", content="甲" * 200),
        Message(role="system", content="突然插进来的规矩"),
        Message(role="user", content="乙" * 200),
    ]
    with pytest.raises(DuGentXError) as info:
        await BasicCompactor().compact(_ctx(), messages, budget_tokens=10)
    assert "system" in str(info.value)


# ------------------------------------------------------------------ 不变量


async def test_an_orphaned_tool_result_is_refused_loudly() -> None:
    """没有对应调用的工具结果会让整个请求被拒——这是压缩最容易犯的错。"""
    bad = [
        Message(role="user", content="读一下 config.py"),
        Message(role="tool", content="……", tool_call_id="c9"),
    ]
    with pytest.raises(DuGentXError) as info:
        assert_tool_pairing(bad)
    assert "c9" in str(info.value)

    with pytest.raises(DuGentXError):
        await BasicCompactor().compact(_ctx(), bad, budget_tokens=1)


async def test_a_compaction_pass_reduces_the_token_estimate_and_keeps_pairs_intact() -> None:
    log = _conversation()
    view = derive_view(log)
    result = await BasicCompactor(keep_recent_messages=4).compact(
        _ctx(), view, budget_tokens=1500
    )

    assert result.before_tokens == messages_tokens(view)
    assert result.after_tokens == messages_tokens(result.messages)
    assert result.after_tokens < result.before_tokens
    assert result.kept_head == result.messages[:3]
    assert_tool_pairing(result.messages)


# ------------------------------------------------------------------ 插件


async def test_the_plugin_records_its_decision_and_the_projection_still_verifies() -> None:
    """压缩必须留下一条能重建投影的事件，否则「模型可见 ⟺ 已记录」就是空话。"""
    log = _conversation()
    ctx = _ctx(session=log)
    dispose = await ctx.mount(
        create_compaction(
            {
                "keep_recent_tool_lines": 6,
                "keep_recent_messages": 2,
                "context_budget_tokens": 1200,
            }
        )
    )
    compacted: list[int] = []
    ctx.on("context/compacted", lambda result: compacted.append(result.dropped))

    before = derive_view(log)
    out = await ctx.events.waterfall("agent/pre-step", before, terminal=_identity)

    event = log.last("context/compacted")
    assert event is not None
    assert set(event.data) >= {
        "drop_before_seq",
        "summary",
        "dropped",
        "summarized",
        "before_tokens",
        "after_tokens",
        "note",
    }
    assert event.data["dropped"] > 0
    assert event.data["after_tokens"] < event.data["before_tokens"]

    # 模型将要看到的东西，仍然能从日志重算出来——这是压缩最难的那条要求。
    verify_projection(out, log)
    assert out[0].content.endswith(event.data["summary"])
    assert_tool_pairing(out)
    assert len(out) < len(before)
    assert messages_tokens(out) < messages_tokens(before)
    assert compacted == [event.data["dropped"]]

    dispose()
    assert not ctx.has("compaction")


async def test_a_second_pass_carries_the_previous_summary_forward() -> None:
    """第二次压缩不能把第一次的摘要当普通旧消息丢首句——那是静默的信息丢失。"""
    log = _conversation()
    compactor = BasicCompactor(keep_recent_messages=2)
    ctx = _ctx(session=log)

    view = derive_view(log)
    first = await compactor.compact(ctx, view, budget_tokens=1200)
    cutoff, summary = encode_drop(log, view, first)
    marker = "顺序反了：先读 yaml 再加载 .env。"
    assert marker in summary
    log.append(
        "context/compacted",
        drop_before_seq=cutoff,
        summary=summary,
        dropped=first.dropped,
        summarized=first.summarized,
        before_tokens=first.before_tokens,
        after_tokens=first.after_tokens,
        note=first.note,
    )

    # 会话继续长，再来一次大的工具结果，然后把预算压到很低。
    log.append(
        "assistant/message",
        content="",
        tool_calls=[
            {"id": "c3", "name": "read_file", "arguments": {"path": "tests/test_config.py"}}
        ],
    )
    log.append("tool/result", call_id="c3", name="read_file", content=_noise(400))
    log.append("assistant/message", content="测试也过了。")

    second = await compactor.compact(ctx, derive_view(log), budget_tokens=400)
    assert second.changed
    assert marker in second.messages[0].content
    # 带下去的是内容，不是又套一层的摘要标记——摘要套摘要套到第三层就没人看得懂了。
    assert "【以下是更早对话的摘要" not in second.messages[0].content
