"""session 缝的测试：投影、守门断言、JSONL 存取，以及 session 插件的落盘时机。

这一层的价值全在「模型可见 ⟺ 已记录」这条规则上，所以测试也围着它转：
日志能重建出消息、重建不出来的组合要被当场抓住、写进文件的日志读回来必须一模一样。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dugentx.kernel.context import Context
from dugentx.kernel.errors import DuGentXError
from dugentx.plugins.session import create as session_plugin
from dugentx.seams.messages import Message
from dugentx.seams.session import (
    JsonlSessionStore,
    SessionLog,
    derive_messages,
    verify_projection,
)

# -------------------------------------------------------------------- 投影


def _scripted_log() -> SessionLog:
    """一份有人味的日志：用户说了话、模型请求了工具、工具回了结果，中间夹着元数据。"""
    log = SessionLog("round-trip")
    log.append("session/start", label="main", model="deepseek-chat")
    log.append("turn/start", prompt="帮我看看 a.txt")
    log.append("user/message", content="帮我看看 a.txt")
    log.append("step/start", index=0)
    log.append(
        "assistant/message",
        content="",
        tool_calls=[{"id": "call_1", "name": "read_file", "arguments": {"path": "a.txt"}}],
        total_tokens=12,
    )
    log.append("tool/result", call_id="call_1", name="read_file", content="hello", ok=True)
    log.append("context/injected", content="当前分支：main")
    log.append("assistant/message", content="里面写着 hello")
    log.append("turn/end", stop_reason="completed", steps=2)
    return log


def test_append_and_derive_messages_round_trip() -> None:
    """投影只认模型可见的事件；生命周期事件不进请求。"""
    messages = derive_messages(_scripted_log())

    assert [(m.role, m.content) for m in messages] == [
        ("user", "帮我看看 a.txt"),
        ("assistant", ""),
        ("tool", "hello"),
        ("user", "当前分支：main"),
        ("assistant", "里面写着 hello"),
    ]
    assert messages[1].tool_calls[0].name == "read_file"
    assert messages[1].tool_calls[0].arguments == {"path": "a.txt"}
    assert messages[2].tool_call_id == "call_1"
    assert len(messages) == 5  # 元数据事件（步数、回合边界）一条都没漏进来


def test_derive_messages_is_a_pure_projection_of_the_log() -> None:
    """投影是纯函数：同样的日志投影两次必须一样，投影本身不改日志。"""
    log = _scripted_log()
    before = [e.to_json() for e in log.events()]
    assert derive_messages(log) == derive_messages(log)
    assert [e.to_json() for e in log.events()] == before


def test_verify_projection_passes_on_a_consistent_pair() -> None:
    log = _scripted_log()
    verify_projection(derive_messages(log), log)  # 不抛就是过


def test_verify_projection_catches_a_message_that_was_never_logged() -> None:
    """往请求里塞一条没记录的东西，必须当场炸。"""
    log = _scripted_log()
    smuggled = [*derive_messages(log), Message(role="user", content="偷偷加的")]

    with pytest.raises(DuGentXError) as excinfo:
        verify_projection(smuggled, log)
    assert "模型可见" in str(excinfo.value)


def test_verify_projection_catches_an_edited_message() -> None:
    """条数对得上、内容被改过，同样要炸——否则「重建」就是句空话。"""
    log = _scripted_log()
    tampered = derive_messages(log)
    tampered[1].content = "我改过这句话"

    with pytest.raises(DuGentXError) as excinfo:
        verify_projection(tampered, log)
    assert "第 1 条" in str(excinfo.value)


def test_verify_projection_catches_a_dropped_message() -> None:
    log = _scripted_log()
    with pytest.raises(DuGentXError):
        verify_projection(derive_messages(log)[:-1], log)


# -------------------------------------------------------------------- JSONL


def test_jsonl_store_saves_and_loads_a_session_unchanged(session_store: JsonlSessionStore) -> None:
    log = _scripted_log()
    session_store.save(log)

    loaded = session_store.load(log.session_id)
    assert [e.to_json() for e in loaded] == [e.to_json() for e in log]
    assert loaded.session_id == log.session_id
    assert loaded.count("assistant/message") == 2
    assert loaded.usage().total_tokens == 12
    assert session_store.list_sessions() == [log.session_id]


def test_jsonl_store_appends_one_event_at_a_time(session_store: JsonlSessionStore) -> None:
    """追加写：崩溃只会坏最后一行，前面的事件还在。"""
    log = SessionLog("appended")
    session_store.save(log)
    first = log.append("user/message", content="第一句")
    session_store.append_event(log.session_id, first)
    second = log.append("user/message", content="第二句")
    session_store.append_event(log.session_id, second)

    loaded = session_store.load("appended")
    assert [e.data["content"] for e in loaded] == ["第一句", "第二句"]
    assert [e.seq for e in loaded] == [0, 1]


def test_jsonl_store_refuses_an_unknown_session(session_store: JsonlSessionStore) -> None:
    with pytest.raises(DuGentXError):
        session_store.load("never-existed")


# -------------------------------------------------------------------- 插件


async def test_session_plugin_provides_log_and_store(
    ctx: Context, session_dir: Path, session_store: JsonlSessionStore
) -> None:
    dispose = await ctx.mount(
        session_plugin({"directory": str(session_dir), "session_id": "s1"})
    )
    try:
        log = ctx.service("session")
        assert log.session_id == "s1"
        assert ctx.service("sessionStore") is not None
        assert len(log) == 0
    finally:
        dispose()


async def test_session_plugin_flushes_the_log_at_turn_boundaries(
    ctx: Context, session_dir: Path, session_store: JsonlSessionStore
) -> None:
    """循环只管往日志里写，落盘是这里的事——所以它不知道持久化的存在。"""
    dispose = await ctx.mount(
        session_plugin({"directory": str(session_dir), "session_id": "s2"})
    )
    try:
        log = ctx.service("session")
        log.append("turn/start", prompt="你好")
        log.append("user/message", content="你好")
        log.append("assistant/message", content="你也好")
        assert not session_store.path_for("s2").exists()  # 还没到回合边界，不该写盘

        ctx.events.emit("turn/end", None)  # 载荷这里不看，它只是个信号

        # 写下去的是整份日志，不只是「这一刻之后」的那些事件。
        saved = session_store.load("s2")
        assert [e.kind for e in saved] == ["turn/start", "user/message", "assistant/message"]
    finally:
        dispose()


async def test_a_boot_that_did_no_work_leaves_no_session_file(
    ctx: Context, session_dir: Path, session_store: JsonlSessionStore
) -> None:
    """只读命令也会启动运行时；启动就落盘会让「我有几个会话」变成一个越问越错的数字。"""
    dispose = await ctx.mount(
        session_plugin({"directory": str(session_dir), "session_id": "idle"})
    )
    ctx.events.emit("session/start", "main", "")  # 载荷按事件目录的字段顺序传
    assert not session_store.path_for("idle").exists()

    ctx.events.emit("session/end")
    assert not session_store.path_for("idle").exists()

    dispose()  # 卸载时的那次 flush 同样要过这道闸
    assert not session_store.path_for("idle").exists()


async def test_session_plugin_flushes_on_unmount_and_stops_listening(
    ctx: Context, session_dir: Path, session_store: JsonlSessionStore
) -> None:
    dispose = await ctx.mount(
        session_plugin({"directory": str(session_dir), "session_id": "s3"})
    )
    log = ctx.service("session")
    log.append("turn/start", prompt="最后一句")
    log.append("assistant/message", content="卸载前最后一句")

    dispose()

    assert [e.kind for e in session_store.load("s3")] == ["turn/start", "assistant/message"]
    assert ctx.get("session") is None
    # 监听器必须跟着走：插件拔掉之后还在写盘的代码就是幽灵。
    assert ctx.events.listeners("turn/end") == []
    assert ctx.events.listeners("session/end") == []


async def test_session_plugin_resumes_an_existing_session(
    ctx: Context, session_dir: Path, session_store: JsonlSessionStore
) -> None:
    previous = SessionLog("resume-me")
    previous.append("user/message", content="上一轮说过的话")
    previous.append("assistant/message", content="上一轮的答复")
    session_store.save(previous)

    dispose = await ctx.mount(
        session_plugin({"directory": str(session_dir), "session_id": "resume-me"})
    )
    try:
        resumed = ctx.service("session")
        assert resumed.session_id == "resume-me"
        assert [m.content for m in derive_messages(resumed)] == ["上一轮说过的话", "上一轮的答复"]
        verify_projection(derive_messages(resumed), resumed)
    finally:
        dispose()


async def test_session_plugin_starts_fresh_when_the_id_is_new(
    ctx: Context, session_dir: Path
) -> None:
    dispose = await ctx.mount(
        session_plugin({"directory": str(session_dir), "session_id": "brand-new"})
    )
    try:
        log = ctx.service("session")
        assert log.session_id == "brand-new"
        assert len(log) == 0
    finally:
        dispose()


async def test_session_plugin_invents_an_id_when_none_is_configured(
    ctx: Context, session_dir: Path
) -> None:
    dispose = await ctx.mount(session_plugin({"directory": str(session_dir)}))
    try:
        log = ctx.service("session")
        assert log.session_id
        assert log.session_id != "None"
    finally:
        dispose()
