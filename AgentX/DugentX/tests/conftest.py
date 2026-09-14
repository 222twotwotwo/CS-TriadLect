"""共享夹具：所有测试都在**离线、无 key** 的前提下跑。

这两条是硬要求，所以它们被钉在夹具里，而不是指望每个测试自觉：

- **不联网**：模型这一层要么用回放适配器，要么用 `fake_any_llm` 替换掉
  `any_llm.acompletion`。测试里不允许出现真实的 provider 调用。
- **不用 key**：`no_api_key` 是 autouse 的，把常见的 key 变量从环境里摘掉。
  一个「本机恰好配了 key」才通过的测试等于没有测试——它会在 CI 上红，
  或者更糟：在别人机器上绿。

`fake_any_llm` 造的分片用的是 **any-llm 自己的类型**（`ChatCompletionChunk`、
`ChoiceDelta`……），不是随手捏的 SimpleNamespace。字段名漂了要能被测试抓住，
而字段名正是这个适配器最脆弱的地方。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import any_llm
import pytest
from any_llm.types.completion import (
    ChatCompletionChunk,
    ChoiceDelta,
    ChoiceDeltaToolCall,
    ChoiceDeltaToolCallFunction,
    ChunkChoice,
    CompletionUsage,
)

from dugentx.events import EVENT_MODES
from dugentx.kernel.context import Context
from dugentx.kernel.events import EventBus
from dugentx.seams.session import JsonlSessionStore

API_KEY_ENV_VARS = (
    "OPENAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "MISTRAL_API_KEY",
    "GROQ_API_KEY",
    "XAI_API_KEY",
    "TOGETHER_API_KEY",
    "MOONSHOT_API_KEY",
)
"""任何一个存在都可能让某个 provider 真的发得出请求。全部摘掉。"""


@pytest.fixture(autouse=True)
def no_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """把环境清成「没配过任何 key」的样子。"""
    for name in API_KEY_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("DUGENTX_TEST_KEY", raising=False)


@pytest.fixture
def ctx() -> Context:
    """一个根上下文，带**真实的事件目录**。

    事件总线拿到目录后会在派发时校验事件名和派发方式——测试里也照此运行，
    这样「事件名写错」在测试阶段就会炸出来，而不是等到运行期安静地不生效。
    """
    return Context("test", events=EventBus(EVENT_MODES))


@pytest.fixture
def session_dir(tmp_path: Path) -> Path:
    """一块干净的、放会话 JSONL 的目录。"""
    return tmp_path / "sessions"


@pytest.fixture
def session_store(session_dir: Path) -> JsonlSessionStore:
    return JsonlSessionStore(session_dir)


class FakeAnyLlm:
    """替身版的 `any_llm.acompletion`：记下调用参数，按排好的脚本吐分片。

    `calls` 是断言「到底发了什么出去」的地方——wire 翻译的正确性全靠它。
    测试里不关心真实 provider 会怎么响应，只关心我们**发出去的形状**对不对。
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.script: list[list[Any]] = []
        self.error: BaseException | None = None
        self.streamed = 0

    # ------------------------------------------------------------ 造分片

    @staticmethod
    def chunk(
        *,
        content: str | None = None,
        reasoning: str | None = None,
        tool_calls: tuple[ChoiceDeltaToolCall, ...] = (),
        usage: CompletionUsage | None = None,
    ) -> ChatCompletionChunk:
        """造一片 provider 的流分片；`usage` 可以单独成片（真 provider 就是这样）。"""
        delta = ChoiceDelta(
            content=content,
            reasoning=reasoning,
            tool_calls=list(tool_calls) or None,
        )
        has_delta = content is not None or reasoning is not None or bool(tool_calls)
        return ChatCompletionChunk(
            id="chunk",
            created=0,
            model="fake-model",
            object="chat.completion.chunk",
            # 真实 provider 的「只有 usage」那一片是 choices 为空的，这里照抄这个形状：
            # 适配器必须能在没有 choices 的分片上活下来。
            choices=[ChunkChoice(index=0, delta=delta)] if has_delta else [],
            usage=usage,
        )

    @staticmethod
    def fragment(
        index: int,
        *,
        id: str | None = None,
        name: str | None = None,
        arguments: str = "",
    ) -> ChoiceDeltaToolCall:
        return ChoiceDeltaToolCall(
            index=index,
            id=id,
            function=ChoiceDeltaToolCallFunction(name=name, arguments=arguments),
        )

    @staticmethod
    def usage(
        prompt: int = 0, completion: int = 0, total: int = 0
    ) -> CompletionUsage:
        return CompletionUsage(
            prompt_tokens=prompt, completion_tokens=completion, total_tokens=total
        )

    def queue(self, *chunks: Any) -> None:
        """排一次调用的分片；排几次就够几次调用。"""
        self.script.append(list(chunks))

    # ------------------------------------------------------------ 被调用

    async def __call__(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        chunks = self.script.pop(0) if self.script else []

        async def stream() -> Any:
            for chunk in chunks:
                self.streamed += 1
                yield chunk

        return stream()


@pytest.fixture
def fake_any_llm(monkeypatch: pytest.MonkeyPatch) -> FakeAnyLlm:
    """把 `any_llm.acompletion` 换成替身。适配器照常 import any_llm，不需要改代码。"""
    fake = FakeAnyLlm()
    monkeypatch.setattr(any_llm, "acompletion", fake)
    return fake
