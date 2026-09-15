"""compaction 缝 —— 上下文是有预算的。

模型每次只看得到有限的一段对话，而 Agent 跑久了必然超。三种应对，各丢东西：

- **截断**：丢掉最早的消息。便宜、无损，但常常把前提一起丢掉——
  用户在第一句说的「不要动生产配置」，正好是第一个被丢的。
- **摘要**：把前面压成一段。省地方、保语义，但会失真，而且失真不可见。
- **检索**：按当前话题捞回相关片段。省得最多，但需要「拿什么去搜」，
  而那本身又是一次模型调用。

真话是：这三件事都要做，而且要看得到做了什么。所以 DugentX 把压缩
做成一个**在投影这一步发生的操作**：日志不动，压缩只影响「这一轮发给模型什么」。
于是压缩是可审计的（日志里能看见什么时候压过、丢了什么），
也是可回退的（下次可以换一个预算重新压）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from dugentx.kernel.context import Context
from dugentx.seams.messages import Message


@dataclass(slots=True)
class CompactionResult:
    """压缩结果。`dropped` / `summarized` 是给日志和用户看的，不是内部细节。"""

    messages: list[Message]
    dropped: int = 0
    summarized: int = 0
    before_tokens: int = 0
    after_tokens: int = 0
    note: str = ""
    kept_head: list[Message] = field(default_factory=list)
    """压缩后仍然保留的最早那几条，用于向用户展示「前提还在不在」。"""

    @property
    def changed(self) -> bool:
        return self.dropped > 0 or self.summarized > 0


@runtime_checkable
class Compactor(Protocol):
    """`ctx.compaction`。"""

    async def compact(
        self,
        ctx: Context,
        messages: list[Message],
        *,
        budget_tokens: int,
    ) -> CompactionResult: ...


def estimate_tokens(text: str) -> int:
    """粗估 token 数。

    故意用最笨的办法：中文按字符数、英文按 4 字符一 token。
    精确计数要调用 provider 的 tokenizer，而**预算判断不需要精确**——
    它只需要在快满的时候比真实值更保守一点。一个能在离线环境跑、
    不发网络请求的估算函数，比一个精确但需要联网的更有用。
    """
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    other = len(text) - cjk
    return cjk + max(1, other // 4) if other else cjk


def messages_tokens(messages: list[Message]) -> int:
    total = 0
    for message in messages:
        total += estimate_tokens(message.content) + 6  # 每条消息的固定开销
        for call in message.tool_calls:
            total += estimate_tokens(call.name) + estimate_tokens(call.arguments_json) + 8
    return total
