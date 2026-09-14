"""any-llm 适配器的测试：wire 翻译对不对，**一次网络都不发**。

`fake_any_llm`（见 `conftest.py`）把 `any_llm.acompletion` 换成替身，所以这里断言的
是「我们发出去什么、我们收进来怎么翻译」——正是这个文件存在的全部理由。
替身造分片用的是 any-llm 的真实类型，字段名漂了要能被这里抓住。
"""

from __future__ import annotations

import json

import pytest
from conftest import FakeAnyLlm  # tests/ 不是包，所以按模块名导入夹具文件

from dugentx.kernel.errors import PluginError
from dugentx.providers.llm_anyllm import AnyLlmAdapter
from dugentx.seams.llm import LlmRequest, drain_stream
from dugentx.seams.messages import Message, ToolCall


def _request(**kwargs: object) -> LlmRequest:
    """一个最小的请求：一句用户输入。"""
    base: dict[str, object] = {
        "model": "deepseek-chat",
        "messages": [Message(role="user", content="读一下 a.txt")],
    }
    base.update(kwargs)
    return LlmRequest(**base)  # type: ignore[arg-type]


# -------------------------------------------------------------------- 构造


def test_construction_without_model_or_provider_is_loud() -> None:
    """配置错在装载期就该报，不要等到用户面前变成「模型不太行」。"""
    with pytest.raises(PluginError) as no_model:
        AnyLlmAdapter(provider="deepseek")
    assert "model" in str(no_model.value)

    with pytest.raises(PluginError) as no_provider:
        AnyLlmAdapter(model="deepseek-chat")
    assert "provider" in str(no_provider.value)


def test_api_key_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DUGENTX_TEST_KEY", "sk-not-a-real-key")
    adapter = AnyLlmAdapter(model="m", provider="p", api_key_env="DUGENTX_TEST_KEY")
    assert adapter.params_for(_request())["api_key"] == "sk-not-a-real-key"

    # 没配就不传，让 any-llm 用它自己约定的变量（OPENAI_API_KEY 之类）去找。
    assert "api_key" not in AnyLlmAdapter(model="m", provider="p").params_for(_request())


def test_a_named_but_missing_api_key_variable_is_a_plugin_error() -> None:
    """配了变量名却取不到值，是配置错了，不是模型错了。"""
    with pytest.raises(PluginError) as excinfo:
        AnyLlmAdapter(model="m", provider="p", api_key_env="DUGENTX_ABSENT_KEY")
    assert "DUGENTX_ABSENT_KEY" in str(excinfo.value)


# -------------------------------------------------------------------- 参数翻译


def test_params_carry_the_wire_messages() -> None:
    """`to_wire()` 的输出就是发出去的 shapes：role / content / tool_calls。"""
    request = _request(
        messages=[
            Message(role="system", content="你是助手"),
            Message(role="user", content="读一下 a.txt"),
            Message(
                role="assistant",
                tool_calls=[ToolCall(id="call_1", name="read_file", arguments={"path": "a.txt"})],
            ),
            Message(role="tool", content="里面写着 hello", tool_call_id="call_1"),
        ]
    )
    wire = AnyLlmAdapter(model="m", provider="p").params_for(request)["messages"]

    assert wire[0] == {"role": "system", "content": "你是助手"}
    assert wire[1] == {"role": "user", "content": "读一下 a.txt"}
    assert wire[2]["role"] == "assistant"
    assert wire[2]["content"] is None  # 只请求工具、没有正文时，content 是 null
    assert wire[2]["tool_calls"][0]["type"] == "function"
    assert wire[2]["tool_calls"][0]["id"] == "call_1"
    assert wire[2]["tool_calls"][0]["function"]["name"] == "read_file"
    assert json.loads(wire[2]["tool_calls"][0]["function"]["arguments"]) == {"path": "a.txt"}
    assert wire[3] == {"role": "tool", "content": "里面写着 hello", "tool_call_id": "call_1"}


def test_params_pass_provider_model_tools_and_stream_flags() -> None:
    spec = {"type": "function", "function": {"name": "read_file", "parameters": {}}}
    adapter = AnyLlmAdapter(model="m", provider="deepseek")
    params = adapter.params_for(_request(tools=[spec]))

    assert params["model"] == "deepseek-chat"
    assert params["provider"] == "deepseek"
    assert params["stream"] is True
    # usage 是记账的本钱：不主动要，很多 provider 干脆不给。
    assert params["stream_options"] == {"include_usage": True}
    assert params["tools"] == [spec]
    assert "tools" not in adapter.params_for(_request())


def test_request_values_override_the_configured_defaults() -> None:
    adapter = AnyLlmAdapter(model="m", provider="p", temperature=0.1, max_tokens=100)

    from_request = adapter.params_for(_request(temperature=0.9, max_tokens=7))
    assert from_request["temperature"] == 0.9
    assert from_request["max_tokens"] == 7

    from_config = adapter.params_for(_request(model=""))
    assert from_config["model"] == "m"  # 请求没给模型名时退回配置里的那个
    assert from_config["temperature"] == 0.1
    assert from_config["max_tokens"] == 100


def test_reasoning_effort_is_only_sent_when_it_is_set() -> None:
    """any-llm 的默认值是 "auto"（映射到各家的默认档）；显式传 None 反而可能关掉思考。"""
    adapter = AnyLlmAdapter(model="m", provider="p")
    assert "reasoning_effort" not in adapter.params_for(_request())
    assert adapter.params_for(_request(reasoning_effort="medium"))["reasoning_effort"] == "medium"

    configured = AnyLlmAdapter(model="m", provider="p", reasoning_effort="low")
    assert configured.params_for(_request())["reasoning_effort"] == "low"
    assert configured.params_for(_request(reasoning_effort="high"))["reasoning_effort"] == "high"


# -------------------------------------------------------------------- 流的翻译


async def test_stream_translates_chunks_into_deltas(fake_any_llm: FakeAnyLlm) -> None:
    adapter = AnyLlmAdapter(model="m", provider="p")
    frag = fake_any_llm.fragment
    fake_any_llm.queue(
        fake_any_llm.chunk(reasoning="先看看这个文件"),
        fake_any_llm.chunk(content="好，"),
        fake_any_llm.chunk(content="我读一下"),
        fake_any_llm.chunk(
            tool_calls=(frag(0, id="call_abc", name="read_file", arguments='{"pa'),)
        ),
        fake_any_llm.chunk(tool_calls=(frag(0, arguments='th": "a.txt"}'),)),
        fake_any_llm.chunk(usage=fake_any_llm.usage(prompt=11, completion=7, total=18)),
    )

    deltas = [delta async for delta in adapter.stream(_request())]

    assert [delta.text for delta in deltas if delta.text] == ["好，", "我读一下"]
    assert [delta.reasoning for delta in deltas if delta.reasoning] == ["先看看这个文件"]

    fragments = [delta for delta in deltas if delta.is_tool_call]
    # 关键：**续片也带着同一个 id**。少了它，参数会被拼到错误的调用上（或整片丢掉）。
    assert [fragment.tool_call_id for fragment in fragments] == ["call_abc", "call_abc"]
    assert fragments[0].tool_name == "read_file"
    assert fragments[1].tool_name is None
    assert "".join(fragment.arguments_delta for fragment in fragments) == '{"path": "a.txt"}'

    usage = next(delta.usage for delta in deltas if delta.usage is not None)
    assert (usage.prompt_tokens, usage.completion_tokens, usage.total_tokens) == (11, 7, 18)
    assert fake_any_llm.calls[0]["stream"] is True
    assert fake_any_llm.calls[0]["model"] == "deepseek-chat"


async def test_fragmented_tool_arguments_survive_the_seam(fake_any_llm: FakeAnyLlm) -> None:
    """两次工具调用的分片交替到达（真实 provider 就是这样），拼完必须各归各位。"""
    adapter = AnyLlmAdapter(model="m", provider="p")
    frag = fake_any_llm.fragment
    fake_any_llm.queue(
        fake_any_llm.chunk(content="我先看两个地方"),
        fake_any_llm.chunk(tool_calls=(frag(0, id="call_a", name="read_file", arguments='{"pa'),)),
        fake_any_llm.chunk(tool_calls=(frag(1, id="call_b", name="list_dir", arguments='{"pa'),)),
        fake_any_llm.chunk(tool_calls=(frag(0, arguments='th": "a.txt"}'),)),
        fake_any_llm.chunk(tool_calls=(frag(1, arguments='th": "."}'),)),
    )

    turn = await drain_stream(adapter, _request())

    assert turn.content == "我先看两个地方"
    assert [(call.id, call.name, call.arguments) for call in turn.tool_calls] == [
        ("call_a", "read_file", {"path": "a.txt"}),
        ("call_b", "list_dir", {"path": "."}),
    ]


async def test_fragments_without_any_id_still_stitch(fake_any_llm: FakeAnyLlm) -> None:
    """少数 provider 全程不给 id：也要按 index 拼起来，不能把参数丢掉。"""
    adapter = AnyLlmAdapter(model="m", provider="p")
    frag = fake_any_llm.fragment
    fake_any_llm.queue(
        fake_any_llm.chunk(tool_calls=(frag(0, name="read_file", arguments='{"pa'),)),
        fake_any_llm.chunk(tool_calls=(frag(0, arguments='th": "a.txt"}'),)),
    )

    turn = await drain_stream(adapter, _request())
    assert [call.arguments for call in turn.tool_calls] == [{"path": "a.txt"}]


async def test_a_late_id_does_not_split_one_call_in_two(fake_any_llm: FakeAnyLlm) -> None:
    """少数网关第一片不带 id、第二片才带。中途换 id 会让参数丢一半——这曾经是个真 bug。"""
    adapter = AnyLlmAdapter(model="m", provider="p")
    frag = fake_any_llm.fragment
    fake_any_llm.queue(
        fake_any_llm.chunk(tool_calls=(frag(0, name="read_file", arguments='{"pa'),)),
        fake_any_llm.chunk(tool_calls=(frag(0, id="call_late", arguments='th": "a.txt"}'),)),
    )

    turn = await drain_stream(adapter, _request())

    assert [(call.name, call.arguments) for call in turn.tool_calls] == [
        ("read_file", {"path": "a.txt"})
    ]
    assert len(turn.tool_calls) == 1


async def test_provider_errors_are_not_swallowed(fake_any_llm: FakeAnyLlm) -> None:
    """限流、鉴权失败要原样往上抛，由循环变成 agent/error——不能伪装成「模型没话说」。"""
    adapter = AnyLlmAdapter(model="m", provider="p")
    fake_any_llm.error = RuntimeError("429 rate limited")

    with pytest.raises(RuntimeError, match="429"):
        await drain_stream(adapter, _request())
