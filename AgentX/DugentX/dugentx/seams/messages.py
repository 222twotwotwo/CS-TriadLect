"""模型看到的对话词汇。

DugentX 有自己的消息类型，**不直接使用 any-llm（或任何 provider）的返回对象**。
理由不是洁癖：wire 格式是外部契约，会变；harness 的内部词汇不应该跟着变。
转换只发生在 `providers/llm_anyllm.py` 一个文件里——那是唯一一处
知道 OpenAI 兼容格式长什么样的地方。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["system", "user", "assistant", "tool"]


@dataclass(slots=True)
class ToolCall:
    """模型请求的一次工具调用。

    注意用词：**请求**。模型产出的只是这个对象——工具名和参数。
    真正把它执行掉的是 harness，不是模型。这条区分是整份代码的地基。
    """

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)

    @property
    def arguments_json(self) -> str:
        return json.dumps(self.arguments, ensure_ascii=False)

    def to_wire(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.arguments_json},
        }


@dataclass(slots=True)
class Message:
    """一条消息。四种 role，和所有主流 API 一致。"""

    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None

    def to_wire(self) -> dict[str, Any]:
        wire: dict[str, Any] = {"role": self.role, "content": self.content or None}
        if self.tool_calls:
            wire["tool_calls"] = [c.to_wire() for c in self.tool_calls]
        if self.tool_call_id is not None:
            wire["tool_call_id"] = self.tool_call_id
        return wire

    def text_of(self, limit: int = 200) -> str:
        body = self.content.strip().replace("\n", " ")
        if len(body) > limit:
            body = body[: limit - 1] + "…"
        return (
            f"[{self.role}] {body}"
            if body
            else f"[{self.role}] ({len(self.tool_calls)} 次工具请求)"
        )


@dataclass(slots=True)
class Usage:
    """一次模型调用的开销。harness 必须记账，否则上下文预算无从谈起。"""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
        )


@dataclass(slots=True)
class AssistantTurn:
    """一段模型回复的最终形态：文本 + 它请求的工具 + 开销。"""

    content: str = ""
    reasoning: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)

    def to_message(self) -> Message:
        return Message(role="assistant", content=self.content, tool_calls=list(self.tool_calls))


@dataclass(slots=True)
class Delta:
    """流式增量。

    文本、思考、工具调用参数都是**增量**，拼装是调用方的事。
    这条约定让 adapter 极薄：它只负责把 provider 的流翻译成这里的字段。
    """

    text: str = ""
    reasoning: str = ""
    tool_call_id: str | None = None
    tool_name: str | None = None
    arguments_delta: str = ""
    usage: Usage | None = None

    @property
    def is_tool_call(self) -> bool:
        return self.tool_call_id is not None or self.tool_name is not None


def tool_result_message(call_id: str, content: str) -> Message:
    """把一次工具执行的结果包成回传给模型的消息。"""
    return Message(role="tool", content=content, tool_call_id=call_id)
