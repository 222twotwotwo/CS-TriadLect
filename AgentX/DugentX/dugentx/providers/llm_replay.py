"""回放适配器 —— 一个不联网、不要 key 的模型。

这不是测试脚手架，是一件正经的交付物。理由很直接：一个 harness 如果只有在
交了钱拿到 key 之后才能跑起来，那么**读这个仓库的人永远看不见它跑**，
CI 里也永远只有单元测试。回放适配器把「模型是外部的」这件事变成一个可以
写死的输入，于是整条循环——投影、工具管道、权限、压缩、子 agent——
都能在离线状态下被真正跑一遍。

它同样回答了配置问题：`adapter: replay` 时，脚本就是配置的一部分。
脚本按轮次消费：一次 `stream()` 消耗一轮，轮次用完按 `on_exhausted` 处理。

写脚本的两种写法：

```python
# 1) 代码里（测试常用）
Scripted([[tool_call("read_file", {"path": "a.txt"})], [text("读完了，里面写着 hello")]])

# 2) 配置里（YAML 里写不了 Python 对象，所以有 dict 形式）
# - id: llm
#   plugin: dugentx.plugins.llm
#   config:
#     adapter: replay
#     replay:
#       on_exhausted: repeat
#       turns:
#         - - tool_call: {name: read_file, arguments: {path: a.txt}}
#         - - text: 读完了
```
"""

from __future__ import annotations

import itertools
import json
from collections.abc import AsyncIterator, Iterable
from typing import Any

from dugentx.kernel.errors import DuGentXError
from dugentx.seams.llm import LlmRequest
from dugentx.seams.messages import Delta, Usage

DEFAULT_TURNS: list[list[Any]] = [
    [
        {
            "text": "（回放模式：没有配置脚本，这是默认的一句回复。"
            "给 llm 的配置写 replay.turns 就能改。）"
        }
    ]
]
"""什么脚本都没给的时候用的默认回复。

宁可回一句人话，也不要抛异常：第一次跑起来的人应该看到「回放」，而不是一个 traceback。
"""

_counter = itertools.count(1)
"""给 `tool_call()` 生成 id 用。

工具调用的 id 必须唯一且非空——`assemble()` 靠它把分片归并到一起，
回传给模型的工具结果也靠它和请求对上。计数器在进程内单调，够用且可预期。
"""


def text(value: str) -> Delta:
    """一轮回复里的一段文本。"""
    return Delta(text=value)


def reasoning(value: str) -> Delta:
    """一轮回复里的一段思考（会进 `AssistantTurn.reasoning`，不是正文）。"""
    return Delta(reasoning=value)


def tool_call(
    name: str, arguments: dict[str, Any] | None = None, *, id: str | None = None
) -> Delta:
    """一轮回复里的**一次工具请求**。

    注意用词：这只是「请求」。真正把它执行掉的是 harness，
    这里只是把「模型想要什么」写成数据。
    """
    return Delta(
        tool_call_id=id or f"call_{next(_counter)}",
        tool_name=name,
        arguments_delta=json.dumps(arguments or {}, ensure_ascii=False),
    )


# -------------------------------------------------------------------- 归一化

_ITEM_KEYS = frozenset(
    {"text", "reasoning", "tool_name", "tool_call_id", "arguments_delta", "usage"}
)
"""dict 形式的增量允许出现哪些键。写错键名会报错，因为静默忽略等于脚本悄悄失灵。"""


def normalize_turn(raw: Any) -> list[Delta]:
    """把配置里的一轮（list / dict / 单个 Delta）变成 `list[Delta]`。"""
    if isinstance(raw, (Delta, str, dict)):
        items: Iterable[Any] = [raw]
    else:
        items = list(raw)
    return [_item_to_delta(item) for item in items]


def _item_to_delta(raw: Any) -> Delta:
    if isinstance(raw, Delta):
        return raw
    if isinstance(raw, str):
        return text(raw)
    if not isinstance(raw, dict):
        raise DuGentXError(
            f"回放脚本里出现了 {type(raw).__name__}，只接受 Delta、dict 或 str"
        )

    item = dict(raw)
    call = item.pop("tool_call", None)
    if call is not None:
        # 便捷写法：{"tool_call": "read_file", "arguments": {...}}
        if isinstance(call, dict):
            name = str(call.get("name", ""))
            arguments = item.pop("arguments", None) or call.get("arguments") or {}
        else:
            name = str(call)
            arguments = item.pop("arguments", None) or {}
        if not name:
            raise DuGentXError(f"回放脚本里的 tool_call 没有名字：{raw!r}")
        extra = set(item) - {"id", "tool_call_id"}
        if extra:
            raise DuGentXError(f"回放脚本里的 tool_call 多了不认识的键 {sorted(extra)}：{raw!r}")
        return tool_call(
            name,
            dict(arguments),
            id=str(item.get("id") or item.get("tool_call_id") or f"call_{next(_counter)}"),
        )

    usage = item.pop("usage", None)
    unknown = set(item) - _ITEM_KEYS
    if unknown:
        raise DuGentXError(
            f"回放脚本里的增量有拼错的键 {sorted(unknown)}；"
            f"能用的键是 {sorted(_ITEM_KEYS)} 或 `tool_call`"
        )
    delta = Delta(
        text=str(item.get("text", "") or ""),
        reasoning=str(item.get("reasoning", "") or ""),
        tool_call_id=item.get("tool_call_id"),
        tool_name=item.get("tool_name"),
        arguments_delta=str(item.get("arguments_delta", "") or ""),
    )
    if usage is not None:
        delta.usage = _usage_from(dict(usage))
    return delta


def _usage_from(raw: dict[str, Any]) -> Usage:
    known = {"prompt_tokens", "completion_tokens", "total_tokens"}
    unknown = set(raw) - known
    if unknown:
        raise DuGentXError(
            f"回放脚本里的 usage 有拼错的键 {sorted(unknown)}；能用的键是 {sorted(known)}"
        )
    return Usage(
        prompt_tokens=int(raw.get("prompt_tokens", 0) or 0),
        completion_tokens=int(raw.get("completion_tokens", 0) or 0),
        total_tokens=int(raw.get("total_tokens", 0) or 0),
    )


# -------------------------------------------------------------------- 适配器


class ReplayAdapter:
    """按脚本回话的 `LlmAdapter`。**永不联网。**

    它顺带当探针：每次请求都留在 `requests` 里。想知道「循环到底把什么发给了模型」
    时，不用去打日志——在测试里读这个列表就行。
    """

    name = "replay"

    def __init__(
        self,
        turns: Iterable[Any] | None = None,
        *,
        name: str = "replay",
        on_exhausted: str = "error",
    ) -> None:
        """装载脚本。

        `on_exhausted` 决定脚本用完之后怎么办：

        - `"error"`（默认）：抛错。脚本是测试的输入，提前用完说明测试的预期错了，
          静默续命会让测试以「没报错」的形式骗过你。
        - `"repeat"`：重复最后一轮。给演示和长对话用——离线跑交互式聊天时，
          没人愿意为了多说一句就去改脚本。
        """
        if on_exhausted not in {"error", "repeat"}:
            raise DuGentXError(
                f"on_exhausted 只能是 'error' 或 'repeat'，收到 {on_exhausted!r}"
            )
        scripted = list(DEFAULT_TURNS if turns is None else turns)
        if not scripted:
            raise DuGentXError("回放脚本是空的：至少给一轮，否则第一次调用就没内容")
        self.turns: list[list[Delta]] = [normalize_turn(turn) for turn in scripted]
        self.name = name
        self.on_exhausted = on_exhausted
        self.calls = 0
        self.requests: list[LlmRequest] = []

    @property
    def remaining(self) -> int:
        """脚本还剩几轮。`on_exhausted="repeat"` 时这个数会停在 0，但调用仍然有回复。"""
        return max(0, len(self.turns) - self.calls)

    async def stream(self, request: LlmRequest) -> AsyncIterator[Delta]:
        """产出一轮脚本内容。"""
        index = self.calls
        self.calls += 1
        self.requests.append(request)

        if index < len(self.turns):
            turn = self.turns[index]
        elif self.on_exhausted == "repeat":
            turn = self.turns[-1]
        else:
            raise DuGentXError(
                f"回放脚本只有 {len(self.turns)} 轮，第 {index + 1} 次调用没有内容了；"
                f"补上脚本，或者把 on_exhausted 设成 'repeat'"
            )
        for delta in turn:
            yield delta


class Scripted(ReplayAdapter):
    """测试里的顺手写法：`Scripted([[tool_call("read_file", {...})], [text("好了")]])`。

    和 `ReplayAdapter` 的唯一区别是它把 turns 放在第一个位置参数上，
    名字也直白——测试代码里一眼能看出这不是真的模型。
    """

    def __init__(self, turns: Iterable[Any], **kwargs: Any) -> None:
        super().__init__(turns, **kwargs)
