"""any-llm 适配器 —— **整份代码里唯一 import `any_llm` 的文件。**

DugentX 的立场写在 `seams/llm.py` 里：模型调用是基础设施，不是产品。
所以「OpenAI 兼容格式长什么样」这件事只允许出现在这一个文件里：

- provider 的字段名变了，改这里；
- any-llm 升了一个大版本，改这里；
- harness 的其余部分（循环、工具、会话）只认 `Delta` / `AssistantTurn`，
  它们不知道 wire 格式存在，也就不会被 wire 格式的变动波及。

这个文件里**不写 HTTP**。`acompletion` 是 any-llm 的活，重试、鉴权、
各家 provider 的差异都在它那一侧；我们只做翻译，不做传输。

另一条容易写错的规矩：**这里的异常不许吞**。provider 报错（限流、模型名错、
key 过期）应该原样往上抛，由循环变成 `agent/error` 事件和一次失败的 step。
在这一层 try/except 出一个「空回复」，会把「key 过期」伪装成「模型不想说话」，
那是这个项目里最贵的一类 bug。
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from typing import Any

import any_llm

from dugentx.kernel.errors import PluginError
from dugentx.seams.llm import LlmRequest
from dugentx.seams.messages import Delta, Usage

STREAM_OPTIONS: dict[str, Any] = {"include_usage": True}
"""流式请求的附加开关。

OpenAI 家族的流只有带上它，才会在最后一片里给出 token 用量。不支持的 provider
会在自己的参数转换里把它丢掉（any-llm 的 provider 实现就是这么写的），
所以这里可以无脑带上——记账（`Usage`）是 harness 必须做的事，不能靠运气拿到。
"""


class AnyLlmAdapter:
    """`LlmAdapter` 的默认实现：harness 的一次请求 → any-llm 的一条流。

    两条约定值得点明，因为它们决定了这个类为什么这么薄：

    1. **流是原语。** 非流式调用在这里没有位置。`stream()` 是 async generator，
       逐片 yield `Delta`；拼成完整回复是 `seams/llm.py::assemble` 的活。
    2. **翻译，不解释。** 这里不对模型的输出做任何判断（比如「空回复要不要重试」）。
       判断是策略，策略属于循环和中间件，不属于适配器。
    """

    name = "anyllm"

    def __init__(
        self,
        *,
        model: str = "",
        provider: str = "",
        temperature: float | None = None,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
        api_key_env: str | None = None,
    ) -> None:
        """构造一个适配器；配置不全就在这里大声报错。

        模型名和 provider 是**装载期**就能判定对错的东西，所以在这里抛
        `PluginError`，而不是等第一次请求失败——那时你会在用户面前看到
        「模型不太行」，而不是「配置少了一行」。

        `api_key_env` 里放的是**环境变量的名字**，不是 key 本身：配置会被
        打印、会被写进日志、会被提交进 git，key 不能出现在那里。留空则
        交给 any-llm 用 provider 自己的约定变量（`OPENAI_API_KEY` 之类）去找。
        """
        if not model:
            raise PluginError(
                "anyllm 适配器需要 model：在 llm 的配置里写 `model: <模型名>`"
            )
        if not provider:
            raise PluginError(
                "anyllm 适配器需要 provider：在 llm 的配置里写 `provider: <provider 名>`"
                "（例如 openai / deepseek / ollama）；any-llm 靠它决定怎么发请求"
            )
        self.model = str(model)
        self.provider = str(provider)
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.reasoning_effort = reasoning_effort
        self.api_key_env = api_key_env
        self._api_key = _read_api_key(api_key_env)

    # ---------------------------------------------------------------- 请求

    def params_for(self, request: LlmRequest) -> dict[str, Any]:
        """把一次 `LlmRequest` 翻译成 `acompletion` 的关键字参数。

        单独抽出来是为了可测：翻译规则（哪些字段只在设置时才传、请求里的值
        如何盖过默认值）可以离线断言，不必真的发一次请求。
        """
        params: dict[str, Any] = {
            "model": request.model or self.model,
            "provider": self.provider,
            "messages": request.wire_messages(),
            "stream": True,
            "stream_options": dict(STREAM_OPTIONS),
        }
        if request.tools:
            # 工具 schema 进来时已经是 OpenAI 形式（tools 缝生成的），不需要再翻译。
            params["tools"] = request.tools

        temperature = request.temperature if request.temperature is not None else self.temperature
        if temperature is not None:
            params["temperature"] = temperature

        max_tokens = request.max_tokens if request.max_tokens is not None else self.max_tokens
        if max_tokens is not None:
            params["max_tokens"] = max_tokens

        # `reasoning_effort` 只在**设置了**才传：any-llm 的默认值 "auto" 会映射成
        # 各家 provider 自己的默认档，显式传 None 反而可能被当成「关掉思考」。
        effort = request.reasoning_effort or self.reasoning_effort
        if effort:
            params["reasoning_effort"] = effort

        if self._api_key:
            params["api_key"] = self._api_key
        return params

    async def stream(self, request: LlmRequest) -> AsyncIterator[Delta]:
        """发起一次流式调用，逐片翻译成 `Delta`。

        provider 的错误不在这里捕获：让它往上抛，循环会把它变成一次失败的 step。
        """
        chunks = await any_llm.acompletion(**self.params_for(request))
        # 工具调用的分片归并状态是**每条流自己的**，所以放在生成器里而不是
        # 实例上：同一个适配器可能被并发使用（子 agent、不同会话）。
        ids = _ToolCallIds()
        async for chunk in chunks:
            for delta in translate_chunk(chunk, ids):
                yield delta


# -------------------------------------------------------------------- 翻译


class _ToolCallIds:
    """把「按 index 分片的工具调用」还原成 `assemble()` 认得的样子。

    流式的工具调用是按 index 切片的：只有**第一片**带 `id` 和函数名，后面的片
    只有 index 和一小段参数 JSON。而 `assemble()` 是按 `tool_call_id` 归并的，
    且 `Delta.is_tool_call` 要求 id 或 name 至少有一个——也就是说，一片既没有 id
    又没有名字的增量会被**整片丢掉**。

    所以这里为每个 index 记住一个稳定的 id（真实 id 一出现就换成真实的），
    让同一通调用的每一片都带同一个 id。缺了这一步，多片参数会拼不出来，
    表现为「模型传的参数一直是空的」——一个非常难查的症状。
    """

    def __init__(self) -> None:
        self._by_index: dict[int, str] = {}
        self._last: int | None = None

    def resolve(self, index: int | None, real_id: str | None) -> str:
        if index is None:
            # 极少数 provider 不给 index：那就认为它是上一片的续篇。
            index = self._last if self._last is not None else 0
        self._last = index

        settled = self._by_index.get(index)
        if settled is not None:
            # 这个槽位已经定过 id 了，后面再冒出真实 id 也不换：中途换 id 会让
            # 先到的参数片落到另一次调用上（那一片没有函数名，会被丢掉），
            # 表现为「模型明明给了参数，harness 收到的是空」。
            return settled

        self._by_index[index] = str(real_id) if real_id else f"call_{index}"
        return self._by_index[index]


def translate_chunk(chunk: Any, ids: _ToolCallIds) -> list[Delta]:
    """一个 provider 的流分片 → 零个或多个 `Delta`。

    返回列表而不是单个 `Delta`，是因为一片里可能同时夹着多个工具调用的碎片；
    `Delta` 一次只描述一个调用，硬塞会把参数拼到错误的调用上。

    只读 `.content` 和 `.reasoning`：别的字段名（比如某些 provider 原生的
    `reasoning_content`）由 any-llm 负责归一到这里的名字——这正是引它进来的原因。
    `reasoning` 用 getattr 保护，因为不是每个 provider、每个版本都有。
    """
    out: list[Delta] = []
    choices = getattr(chunk, "choices", None) or []
    if choices:
        piece = getattr(choices[0], "delta", None)
        if piece is not None:
            body = _text_of(getattr(piece, "content", None))
            thinking = _text_of(getattr(piece, "reasoning", None))
            if body or thinking:
                out.append(Delta(text=body, reasoning=thinking))
            for fragment in getattr(piece, "tool_calls", None) or []:
                function = getattr(fragment, "function", None)
                out.append(
                    Delta(
                        tool_call_id=ids.resolve(
                            getattr(fragment, "index", None), getattr(fragment, "id", None)
                        ),
                        tool_name=getattr(function, "name", None) if function else None,
                        arguments_delta=(getattr(function, "arguments", None) or "")
                        if function
                        else "",
                    )
                )

    usage = _usage_of(getattr(chunk, "usage", None))
    if usage is not None:
        # 用量常常单独占最后一片（choices 为空），所以它不依附于 choices。
        out.append(Delta(usage=usage))
    return out


def _text_of(raw: Any) -> str:
    """把 provider 给的文本字段取成 `str`。

    `Delta.text` / `Delta.reasoning` 是要被 `"".join()` 拼起来的，必须是 `str`——
    而 any-llm 把思考内容包成了一个 `Reasoning(content=...)` 对象（纯字符串它也收，
    返回的是对象）。不拆开这一层，拼装时会在 `"".join()` 上炸掉，
    报错信息还指不到真正的原因。这一条就是被测试逼出来的。
    """
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    content = getattr(raw, "content", None)
    if isinstance(content, str):
        return content
    return str(raw)  # 不认识的形状：宁可原样带过去，也不要静默丢掉模型说过的话


def _usage_of(raw: Any) -> Usage | None:
    """provider 的 usage 对象 → harness 的 `Usage`；没有就返回 None。"""
    if raw is None:
        return None
    prompt = int(getattr(raw, "prompt_tokens", 0) or 0)
    completion = int(getattr(raw, "completion_tokens", 0) or 0)
    total = int(getattr(raw, "total_tokens", 0) or 0)
    if not (prompt or completion or total):
        return None
    total = total or prompt + completion
    return Usage(prompt_tokens=prompt, completion_tokens=completion, total_tokens=total)


def _read_api_key(env_name: str | None) -> str | None:
    """从环境里取 key。

    配了变量名却取不到值，是**配置错误**，装载时就报，不要等到第一次请求
    被 provider 回一个 401——那时候错误信息会指向模型，而不是指向配置。
    """
    if not env_name:
        return None
    value = os.environ.get(env_name, "").strip()
    if not value:
        raise PluginError(
            f"配置里指定了 api_key_env={env_name!r}，但环境里没有这个变量（或它是空的）"
        )
    return value
