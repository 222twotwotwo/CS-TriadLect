"""llm 缝 —— 服务定义。

**这是整份代码里唯一允许碰模型的地方，也是唯一允许依赖外部包的地方。**

DugentX 的立场：模型调用是基础设施，不是产品。手写 HTTP 请求意味着你要
自己处理重试、流式分片、各家不同的字段名、工具调用格式、错误码映射——
这些工作没有一样是「我们这家 harness 的独特价值」，而且做错了会以
「模型今天不太行」的形式表现出来，极难查。

所以这里定一条硬边界：

- **`LlmAdapter` 是接口**，只认 `dugentx.seams.messages` 里的词汇；
- **`providers/llm_anyllm.py` 是唯一实现**，唯一 import `any_llm` 的文件；
- 换 provider、换模型、换网关，改的是配置，不是这里的代码。

`ctx.llm` 是一个**适配器注册表**，不是单个 adapter。这么设计是为了两件事：
离线测试可以挂一个回放 adapter（不需要 key，也不联网），
以及同一个进程里可以让不同会话走不同模型。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from dugentx.kernel.effect import Disposer
from dugentx.seams.messages import AssistantTurn, Delta, Message, Usage


@dataclass(slots=True)
class LlmRequest:
    """一次模型请求。harness 侧构造，adapter 侧翻译。"""

    model: str
    messages: list[Message]
    tools: list[dict[str, Any]] = field(default_factory=list)
    """OpenAI 工具格式的 schema 列表；由 tools 缝生成。"""

    temperature: float | None = None
    max_tokens: int | None = None
    reasoning_effort: str | None = None

    def wire_messages(self) -> list[dict[str, Any]]:
        return [m.to_wire() for m in self.messages]


@runtime_checkable
class LlmAdapter(Protocol):
    """模型适配器。

    只需要实现 `stream`。流是这里的原语，不是可选优化——
    真实的 Agent 产品都要能一个字一个字往外吐，也要能中途叫停；
    把非流式当成原语、把流式当成特例，会在后面所有地方留下分叉。
    """

    name: str

    def stream(self, request: LlmRequest) -> AsyncIterator[Delta]:
        """流式产出一段回复。实现方要么是 async generator，要么返回一个。"""
        ...


class LlmRegistry:
    """`ctx.llm` —— 适配器注册表。

    `default_model` 是给 loop 用的兜底：agent 没指定模型时用哪一个。
    放在这里而不是让 loop 去读 llm 插件的配置，是为了不让循环
    依赖任何一个具体 provider 的配置结构。
    """

    def __init__(self, default: str | None = None, default_model: str | None = None) -> None:
        self._adapters: dict[str, LlmAdapter] = {}
        self._default = default
        self.default_model = default_model

    def register(self, adapter: LlmAdapter, *, default: bool = False) -> Disposer:
        name = adapter.name
        self._adapters[name] = adapter
        if default or self._default is None:
            self._default = name

        def dispose() -> None:
            if self._adapters.get(name) is adapter:
                del self._adapters[name]
                if self._default == name:
                    self._default = next(iter(self._adapters), None)

        return dispose

    @property
    def default_name(self) -> str:
        if self._default is None:
            raise LookupError("ctx.llm 里还没有注册任何适配器")
        return self._default

    def use(self, name: str) -> None:
        if name not in self._adapters:
            raise LookupError(f"没有名为 {name!r} 的适配器；已有：{sorted(self._adapters)}")
        self._default = name

    def adapter(self, name: str | None = None) -> LlmAdapter:
        key = name or self.default_name
        try:
            return self._adapters[key]
        except KeyError:
            raise LookupError(
                f"没有名为 {key!r} 的适配器；已有：{sorted(self._adapters)}"
            ) from None

    def names(self) -> list[str]:
        return sorted(self._adapters)


async def drain_stream(adapter: LlmAdapter, request: LlmRequest) -> AssistantTurn:
    """把一条流拼成一个完整的回复。

    拼装逻辑写在缝这一层，而不是每个 adapter 里重写一遍：
    工具调用参数是按片段到达的，能不能正确拼起来，和具体是哪家 provider 无关。
    """
    return await assemble(adapter.stream(request))


async def assemble(chunks: AsyncIterator[Delta]) -> AssistantTurn:
    """把 Delta 流拼成 AssistantTurn。这是 harness 自己的活，不该由 adapter 承担。"""
    parts: list[str] = []
    thinking: list[str] = []
    usage = Usage()
    order: list[str] = []
    calls: dict[str, dict[str, Any]] = {}

    async for delta in chunks:
        if delta.text:
            parts.append(delta.text)
        if delta.reasoning:
            thinking.append(delta.reasoning)
        if delta.usage is not None:
            usage = delta.usage
        if delta.is_tool_call:
            key = delta.tool_call_id or f"slot-{len(order)}"
            if key not in calls:
                calls[key] = {"id": delta.tool_call_id or key, "name": "", "args": ""}
                order.append(key)
            if delta.tool_name:
                calls[key]["name"] = delta.tool_name
            if delta.arguments_delta:
                calls[key]["args"] += delta.arguments_delta

    from dugentx.seams.messages import ToolCall

    tool_calls: list[ToolCall] = []
    for key in order:
        raw = calls[key]
        if not raw["name"]:
            continue
        tool_calls.append(
            ToolCall(id=raw["id"], name=raw["name"], arguments=parse_arguments(raw["args"]))
        )

    return AssistantTurn(
        content="".join(parts),
        reasoning="".join(thinking),
        tool_calls=tool_calls,
        usage=usage,
    )


def parse_arguments(raw: str) -> dict[str, Any]:
    """解析工具调用参数。

    流式拼出来的 JSON 经常不完整（模型被截断、或是分片边界问题），
    这里**不抛异常**：回一个空参数，让工具自己去报「缺参数」。
    工具报错会作为一条 tool 消息回到模型面前，它有机会重试；
    而在这一层抛异常会把整个 step 打断。
    """
    import json

    text = raw.strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
