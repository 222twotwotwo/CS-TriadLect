"""session 缝 —— 只能追加的事件日志，以及从它投影出来的模型历史。

dsh 有一条规则写在架构文档的第一段，DugentX 照抄：

> **模型可见的，就是已记录的。**（Model-visible means logged.）

意思是：发给模型的每一条消息，都必须能从会话日志重建出来。日志不是
「调试用的旁路」，它就是上下文的唯一真相。这条规则一次性解决了三件事：

- 会话持久化 = 把日志写下来，不需要单独的「存对话」逻辑；
- 恢复会话 = 读日志重新投影，不需要模型再被问一遍；
- 上下文压缩 = 在投影这一步做，不改日志，所以压缩是可审计的。

`verify_projection()` 把这个规则变成一行可执行的断言——它在每个 step
之前跑一次。规则如果只写在文档里，三个月后就会有人绕过它。
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from dugentx.kernel.errors import DuGentXError
from dugentx.seams.messages import Message, ToolCall, Usage

EventKind = Literal[
    "session/start",
    "turn/start",
    "turn/end",
    "step/start",
    "step/end",
    "system/message",
    "user/message",
    "assistant/message",
    "tool/request",
    "tool/result",
    "context/injected",
    "context/compacted",
    "plugin/mounted",
    "plugin/unmounted",
    "skill/loaded",
    "session/end",
]

MODEL_VISIBLE: frozenset[str] = frozenset(
    {"system/message", "user/message", "assistant/message", "tool/result", "context/injected"}
)
"""会进入模型请求的事件类型。其余都是生命周期或元数据。

`system/message` 在里面，这一点值得说明：系统提示词是**拼出来的**
（工具清单、技能目录、当前工作区都在里面），所以它会变。会变的东西
更要记——不记，重放一个历史会话时就复现不出当时真正发出去的请求。
工具在会话中途被挂上来时提示词会变，那正是需要留痕的时刻。
"""


@dataclass(slots=True)
class SessionEvent:
    seq: int
    kind: str
    at: float
    data: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {"seq": self.seq, "kind": self.kind, "at": self.at, "data": self.data}

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> SessionEvent:
        return cls(
            seq=int(raw["seq"]),
            kind=str(raw["kind"]),
            at=float(raw["at"]),
            data=dict(raw.get("data") or {}),
        )


class SessionLog:
    """只能追加。没有 update，没有 delete——上下文才有唯一真相。"""

    def __init__(
        self,
        session_id: str | None = None,
        *,
        events: list[SessionEvent] | None = None,
    ) -> None:
        self.session_id = session_id or uuid.uuid4().hex[:12]
        self._events: list[SessionEvent] = list(events or [])

    def append(self, kind: EventKind | str, **data: Any) -> SessionEvent:
        event = SessionEvent(
            seq=len(self._events), kind=str(kind), at=time.time(), data=data
        )
        self._events.append(event)
        return event

    def events(self) -> list[SessionEvent]:
        return list(self._events)

    def of_kind(self, *kinds: str) -> list[SessionEvent]:
        wanted = set(kinds)
        return [e for e in self._events if e.kind in wanted]

    def last(self, kind: str) -> SessionEvent | None:
        for event in reversed(self._events):
            if event.kind == kind:
                return event
        return None

    def count(self, kind: str) -> int:
        return sum(1 for e in self._events if e.kind == kind)

    def usage(self) -> Usage:
        total = Usage()
        for event in self._events:
            if event.kind == "assistant/message":
                total = total + Usage(
                    prompt_tokens=int(event.data.get("prompt_tokens", 0)),
                    completion_tokens=int(event.data.get("completion_tokens", 0)),
                    total_tokens=int(event.data.get("total_tokens", 0)),
                )
        return total

    def __len__(self) -> int:
        return len(self._events)

    def __iter__(self) -> Iterator[SessionEvent]:
        return iter(self._events)


# ------------------------------------------------------------------ 持久化


class JsonlSessionStore:
    """把日志写成 JSONL。一行一个事件，追加写，坏了只坏最后一行。

    这是 `session` 缝的一个 provider。换成 SQLite 只需要实现同样两个方法，
    上层（agent-loop、TUI、恢复会话）一行都不用改——这就是缝的意义。
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, session_id: str) -> Path:
        return self.root / f"{session_id}.jsonl"

    def save(self, log: SessionLog) -> Path:
        import json

        path = self.path_for(log.session_id)
        with path.open("w", encoding="utf-8") as fh:
            for event in log:
                fh.write(json.dumps(event.to_json(), ensure_ascii=False) + "\n")
        return path

    def append_event(self, session_id: str, event: SessionEvent) -> None:
        import json

        with self.path_for(session_id).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event.to_json(), ensure_ascii=False) + "\n")

    def load(self, session_id: str) -> SessionLog:
        import json

        path = self.path_for(session_id)
        if not path.exists():
            raise DuGentXError(f"没有这个会话：{session_id}")
        events = [
            SessionEvent.from_json(json.loads(line))
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        return SessionLog(session_id, events=events)

    def list_sessions(self) -> list[str]:
        return sorted(p.stem for p in self.root.glob("*.jsonl"))


# ------------------------------------------------------------------ 投影


def _project(events: list[SessionEvent]) -> list[Message]:
    """把一组事件投影成消息列表。

    两条规矩，都来自「一次请求里只有一个系统提示词」这个事实：

    1. **只取最后一条 `system/message`。** 系统提示词是拼出来的、会变
       （工具清单变了它就变），但历史里不该堆一排系统消息——
       当前那一条才是真的，前面的只是过程记录。
    2. **它排在最前面。** 顺序是 [system, ...其余...]。把系统消息放在
       用户消息后面，很多 provider 会直接拒绝整个请求；
       而按事件顺序天然就会变成那样——`user/message` 在 `turn/start` 时
       就追加了，系统提示词是到 step 开头才同步的。
    """
    latest_system: SessionEvent | None = None
    rest: list[SessionEvent] = []
    for event in events:
        if event.kind == "system/message":
            latest_system = event
        elif event.kind in MODEL_VISIBLE:
            rest.append(event)

    messages: list[Message] = []
    if latest_system is not None:
        messages.append(
            Message(role="system", content=str(latest_system.data.get("content", "")))
        )
    messages.extend(_events_to_messages(rest))
    return messages


def derive_view(log: SessionLog) -> list[Message]:
    """投影出**这一轮真正发给模型**的消息。

    和 `derive_messages` 的区别，就是压缩。

    压缩不能改写日志——日志是唯一真相，压过就回不来了。所以压缩
    以一条 `context/compacted` 事件的形式记录**它做了什么决定**：

        {"drop_before_seq": 12, "summary": "……", "dropped": 9, ...}

    视图据此重建：丢掉 seq < drop_before_seq 的那些模型可见事件，
    并把摘要作为一条消息放在最前面。于是——

    - 压缩是**可审计的**：日志里看得见什么时候压过、丢了几条、摘要是什么；
    - 压缩是**可回退的**：换一个预算重新压一次，旧决定自动被后一条覆盖；
    - `verify_projection` 依然成立：模型看到的每一句，都能从日志重算出来。

    多次压缩时只认**最后一条**：后一次摘要已经包含了前一次的内容。
    """
    latest = log.last("context/compacted")
    if latest is None:
        return derive_messages(log)

    cutoff = int(latest.data.get("drop_before_seq", 0))
    summary = str(latest.data.get("summary", "")).strip()

    kept = [e for e in log.events() if e.seq >= cutoff]
    view = _project(kept)
    if summary:
        # 摘要紧跟在系统提示词后面、在所有历史前面：
        # 它就是「更早那些消息」的替身。
        at = 1 if view and view[0].role == "system" else 0
        view.insert(
            at,
            Message(
                role="user",
                content=(
                    "【以下是更早对话的摘要，由上下文压缩生成；"
                    "它替代了被丢掉的部分，可能已经失真】\n" + summary
                ),
            ),
        )
    return view


def derive_messages(log: SessionLog) -> list[Message]:
    """从日志投影出完整模型历史（不套用任何压缩）。

    这是整个 harness 最该保持「纯粹」的一个函数：输入是日志，输出是消息，
    没有副作用，没有网络，可以被单测钉死。所有「模型看到了什么」的争议，
    都在这里解决，而不是在循环里加 if。
    """
    return _project(log.events())


def _events_to_messages(events: list[SessionEvent]) -> list[Message]:
    messages: list[Message] = []
    for event in events:
        if event.kind in ("user/message", "context/injected"):
            messages.append(Message(role="user", content=str(event.data.get("content", ""))))
        elif event.kind == "assistant/message":
            calls = [
                ToolCall(
                    id=str(c.get("id", "")),
                    name=str(c.get("name", "")),
                    arguments=dict(c.get("arguments") or {}),
                )
                for c in (event.data.get("tool_calls") or [])
            ]
            messages.append(
                Message(
                    role="assistant",
                    content=str(event.data.get("content", "")),
                    tool_calls=calls,
                )
            )
        elif event.kind == "tool/result":
            messages.append(
                Message(
                    role="tool",
                    content=str(event.data.get("content", "")),
                    tool_call_id=str(event.data.get("call_id", "")),
                )
            )
    return messages


def verify_projection(messages: list[Message], log: SessionLog) -> None:
    """断言：发给模型的消息确实能从日志重建。

    在真实的 harness 里这条会以两种形态存在——开发期一个断言，
    发布版一个遥测计数器。示例项目里保留断言形态，因为它的价值在
    「让后来的人知道有这条规则」，而不在于抓线上事故。
    """
    expected = derive_view(log)
    if len(messages) != len(expected):
        raise DuGentXError(
            f"模型可见 ⟺ 已记录 被破坏了：请求里有 {len(messages)} 条消息，"
            f"日志只能重建出 {len(expected)} 条。"
            f"说明有人往请求里塞了没记录的东西。"
        )
    for i, (got, want) in enumerate(zip(messages, expected, strict=True)):
        if got.role != want.role or got.content != want.content:
            raise DuGentXError(
                f"模型可见 ⟺ 已记录 被破坏了：第 {i} 条消息和日志对不上"
                f"（请求 {got.text_of(60)!r} / 日志 {want.text_of(60)!r}）"
            )
