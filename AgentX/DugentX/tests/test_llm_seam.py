"""llm 缝的测试：拼装、参数解析、适配器注册表，以及我这一侧三个插件的装配行为。

这些测试全部离线：用的模型要么是回放适配器，要么根本没走到模型
（拼装和注册表都是纯函数/纯数据）。「回放适配器能当模型用」这条尤其重要——
`tests/test_llm_anyllm.py` 证明翻译对，这里证明**没有 provider 也能跑完整条缝**。
"""

from __future__ import annotations

from typing import Any

import pytest

from dugentx.kernel.context import Context
from dugentx.kernel.errors import DuGentXError, PluginError
from dugentx.kernel.plugin import Plugin
from dugentx.plugins.llm import create as llm_plugin
from dugentx.plugins.prompt import create as prompt_plugin
from dugentx.providers.llm_anyllm import AnyLlmAdapter
from dugentx.providers.llm_replay import ReplayAdapter, Scripted, reasoning, text, tool_call
from dugentx.seams.llm import (
    LlmAdapter,
    LlmRegistry,
    LlmRequest,
    assemble,
    drain_stream,
    parse_arguments,
)
from dugentx.seams.messages import Delta, Message, Usage
from dugentx.seams.skill import SkillInfo
from dugentx.seams.tools import LABEL_READ, ToolRegistry, tool_from_function

# -------------------------------------------------------------------- 拼装


async def _stream(*deltas: Delta) -> Any:
    for delta in deltas:
        yield delta


async def test_assemble_stitches_text_reasoning_and_fragmented_tool_calls() -> None:
    """一次回复的三个通道（正文、思考、工具调用）要各归各位。

    工具调用的参数是**按片到达**的：第一片带 id 和函数名，后面的片只有一小段
    JSON。这里喂的就是真实 provider 的分片形状，拼错了会表现为「参数一直是空的」。
    """
    turn = await assemble(
        _stream(
            Delta(reasoning="先看看这个文件"),
            Delta(text="好，"),
            Delta(text="我读一下"),
            Delta(tool_call_id="call_1", tool_name="read_file", arguments_delta='{"pa'),
            Delta(tool_call_id="call_1", arguments_delta='th": "a.tx'),
            Delta(tool_call_id="call_1", arguments_delta='t"}'),
            Delta(tool_call_id="call_2", tool_name="list_dir", arguments_delta="{}"),
            Delta(usage=Usage(prompt_tokens=3, completion_tokens=4, total_tokens=7)),
        )
    )

    assert turn.content == "好，我读一下"
    assert turn.reasoning == "先看看这个文件"
    assert [(c.id, c.name, c.arguments) for c in turn.tool_calls] == [
        ("call_1", "read_file", {"path": "a.txt"}),
        ("call_2", "list_dir", {}),
    ]
    assert turn.usage.total_tokens == 7


def test_parse_arguments_returns_empty_dict_instead_of_raising() -> None:
    """截断的 JSON 不该打断 step——回一个空参数，让工具自己去报缺参数。"""
    assert parse_arguments('{"path": "a.tx') == {}
    assert parse_arguments("") == {}
    assert parse_arguments("   ") == {}
    assert parse_arguments("[1, 2]") == {}
    assert parse_arguments('{"path": "a.txt", "start_line": 3}') == {
        "path": "a.txt",
        "start_line": 3,
    }


# -------------------------------------------------------------------- 注册表


class _StubAdapter:
    """一个只会说话的适配器：注册表测的是「谁是谁的默认」，不是翻译。"""

    def __init__(self, name: str) -> None:
        self.name = name

    async def stream(self, request: LlmRequest) -> Any:
        yield Delta(text=self.name)


def test_registry_register_use_and_dispose() -> None:
    registry = LlmRegistry()
    with pytest.raises(LookupError):
        _ = registry.default_name

    first = _StubAdapter("first")
    throwaway = registry.register(first)
    assert registry.default_name == "first"  # 第一个注册的自动成为默认
    assert registry.names() == ["first"]

    registry.register(_StubAdapter("second"), default=True)
    assert registry.default_name == "second"
    assert sorted(registry.names()) == ["first", "second"]

    registry.use("first")
    assert registry.adapter().name == "first"
    assert registry.adapter("second").name == "second"

    throwaway()
    assert registry.names() == ["second"]
    assert registry.default_name == "second"  # 默认被撤掉时会落到还在的那个
    with pytest.raises(LookupError):
        registry.adapter("first")


def test_registry_names_what_it_has_when_asked_for_something_else() -> None:
    registry = LlmRegistry()
    registry.register(_StubAdapter("replay"))
    with pytest.raises(LookupError) as excinfo:
        registry.use("gpt-9")
    assert "replay" in str(excinfo.value)


async def test_replay_adapter_is_a_real_adapter_for_the_seam() -> None:
    """回放适配器不是测试专用的旁路：它满足同一个协议，走同一条 `assemble`。"""
    replay = Scripted(
        [
            [reasoning("想一下"), tool_call("read_file", {"path": "a.txt"})],
            [text("读完了，里面写着 hello")],
        ]
    )
    assert isinstance(replay, LlmAdapter)

    request = LlmRequest(model="anything", messages=[Message(role="user", content="读一下 a.txt")])
    first = await drain_stream(replay, request)
    assert first.reasoning == "想一下"
    assert [call.name for call in first.tool_calls] == ["read_file"]
    assert first.tool_calls[0].arguments == {"path": "a.txt"}
    assert first.tool_calls[0].id  # 工具结果要靠这个 id 回填，空 id 会让结果对不上号

    second = await drain_stream(replay, request)
    assert second.content == "读完了，里面写着 hello"
    assert second.tool_calls == []
    assert replay.calls == 2
    assert replay.requests[0].messages[0].content == "读一下 a.txt"  # 脚本没吃完就够用的探针


async def test_replay_adapter_accepts_the_dict_form_and_flags_typos() -> None:
    """配置里写不出 Python 对象，所以有 dict 形式；写错键名要报错，不能静默为空。"""
    replay = ReplayAdapter(
        [[{"tool_call": "read_file", "arguments": {"path": "b.txt"}}, {"text": "好"}]]
    )
    turn = await drain_stream(
        replay, LlmRequest(model="m", messages=[Message(role="user", content="x")])
    )
    assert turn.tool_calls[0].arguments == {"path": "b.txt"}
    assert turn.content == "好"

    with pytest.raises(DuGentXError) as excinfo:
        ReplayAdapter([[{"txet": "打错了"}]])
    assert "txet" in str(excinfo.value)


async def test_replay_script_exhaustion_is_loud_unless_told_to_repeat() -> None:
    """脚本用完是测试预期错了，默认要炸；`on_exhausted: repeat` 是给演示用的。"""
    request = LlmRequest(model="m", messages=[Message(role="user", content="x")])
    strict = Scripted([[text("只有一轮")]])
    assert (await drain_stream(strict, request)).content == "只有一轮"
    with pytest.raises(DuGentXError) as excinfo:
        await drain_stream(strict, request)
    assert "on_exhausted" in str(excinfo.value)

    repeating = Scripted([[text("再说一遍")]], on_exhausted="repeat")
    for _ in range(3):
        assert (await drain_stream(repeating, request)).content == "再说一遍"

    with pytest.raises(DuGentXError):
        ReplayAdapter([[text("x")]], on_exhausted="ignore")


# -------------------------------------------------------------------- llm 插件


async def test_llm_plugin_registers_the_configured_adapter(ctx: Context) -> None:
    dispose = await ctx.mount(
        llm_plugin({"adapter": "replay", "replay": {"turns": [[{"text": "你好"}]]}})
    )
    try:
        registry = ctx.service("llm")
        assert registry.default_name == "replay"
        assert isinstance(registry.adapter(), ReplayAdapter)
        turn = await drain_stream(
            registry.adapter(), LlmRequest(model="m", messages=[Message(role="user", content="嗨")])
        )
        assert turn.content == "你好"
    finally:
        dispose()


async def test_llm_plugin_reads_flat_anyllm_config(ctx: Context) -> None:
    """anyllm 的配置短，允许平铺在顶层；默认适配器就是它。"""
    dispose = await ctx.mount(llm_plugin({"model": "deepseek-chat", "provider": "deepseek"}))
    try:
        registry = ctx.service("llm")
        adapter = registry.adapter()
        assert isinstance(adapter, AnyLlmAdapter)
        assert (adapter.model, adapter.provider) == ("deepseek-chat", "deepseek")
        assert registry.default_model == "deepseek-chat"  # 循环在没有显式模型名时读它
    finally:
        dispose()


async def test_llm_plugin_fails_loudly_at_load_time(ctx: Context) -> None:
    """配置错了要在装载时炸，不要等到第一次请求才以「模型不太行」的形式暴露。"""
    with pytest.raises(PluginError):
        await ctx.mount(llm_plugin({"adapter": "anyllm"}))

    with pytest.raises(PluginError) as excinfo:
        await ctx.mount(llm_plugin({"adapter": "gpt-5-turbo"}))
    assert "replay" in str(excinfo.value)  # 报错要说清楚有哪些可选


async def test_llm_plugin_unmount_takes_the_adapter_with_it(ctx: Context) -> None:
    dispose = await ctx.mount(
        llm_plugin({"adapter": "replay", "replay": {"turns": [[{"text": "hi"}]]}})
    )
    registry = ctx.service("llm")
    assert registry.names() == ["replay"]

    dispose()
    assert ctx.get("llm") is None
    assert registry.names() == []  # 卸载要连注册表里的那一笔一起撤销


# -------------------------------------------------------------------- prompt 插件


class _StubSkills:
    async def catalog(self) -> list[SkillInfo]:
        return [
            SkillInfo(name="pdf", description="读 PDF 里的文字", when_to_use="用户给了 .pdf"),
            SkillInfo(name="git", description="查提交历史"),
        ]

    async def load(self, name: str) -> str:
        return f"# {name}"


def read_file(path: str, max_lines: int = 200) -> str:
    """读取工作区里的一个文本文件。

    Args:
        path: 相对于工作区的路径
        max_lines: 最多读多少行
    """
    return ""


def _tools(ctx: Context) -> ToolRegistry:
    """一个只有 `read_file` 的工具注册表：prompt 段要拿它渲染清单。"""
    registry = ToolRegistry(ctx)
    registry.register(tool_from_function(read_file, labels=frozenset({LABEL_READ})))
    return registry


async def _mount_prompt(ctx: Context, config: dict[str, Any] | None = None) -> Any:
    plugin: Plugin = prompt_plugin(config or {})
    return await ctx.mount(plugin)


async def test_prompt_plugin_assembles_sections_in_order_and_degrades_without_skills() -> None:
    ctx = Context("test")
    ctx.provide("tools", _tools(ctx))
    dispose = await _mount_prompt(ctx)
    try:
        prompt = ctx.service("prompt")
        assert prompt.names() == ["extras", "persona", "skills", "tools", "workspace"]

        text = prompt.assemble(ctx)
        # 段落顺序（order）就是读的顺序：先身份、再在哪干活、最后手上有什么。
        assert text.index("harness") < text.index("工作目录：") < text.index("read_file")
        # 没有 skills 服务时整段消失——可选缝缺了不该在提示词里留噪音。
        assert "可以按需加载的技能" not in text
        # 工具清单是从注册表现取的。
        assert "- read_file：读取工作区里的一个文本文件。" in text
    finally:
        dispose()


async def test_prompt_plugin_renders_the_skill_catalog_cached_at_load() -> None:
    ctx = Context("test")
    ctx.provide("tools", _tools(ctx))
    ctx.provide("skills", _StubSkills())
    dispose = await _mount_prompt(ctx)
    try:
        text = ctx.service("prompt").assemble(ctx)
        assert "- pdf：读 PDF 里的文字（什么时候用：用户给了 .pdf）" in text
        assert "- git：查提交历史" in text
    finally:
        dispose()


async def test_prompt_plugin_config_overrides_and_unmount_clears_sections() -> None:
    ctx = Context("test")
    ctx.provide("tools", _tools(ctx))
    dispose = await _mount_prompt(
        ctx, {"persona": "你是测试用的替身。", "workspace": "/tmp/elsewhere", "extras": "别说话。"}
    )
    prompt = ctx.service("prompt")
    text = prompt.assemble(ctx)
    assert text.startswith("你是测试用的替身。")
    assert "elsewhere" in text  # 配置里的工作目录盖过了默认值
    assert text.endswith("别说话。")

    dispose()
    assert ctx.get("prompt") is None
    assert prompt.names() == []
