"""委托的测试：结论回来，过程不回来。

整个缝只有一个主张值得测，就是这句话：子 Agent 的意义是把一段过程挪出
主会话，好让主会话的上下文保持干净。所以最重要的那条断言不是「结果对不对」，
而是「父会话的日志一条都没多」。

这里用一个不碰模型的 driver 顶替 agent 循环：委托的正确性跟模型无关，
而一个需要联网才能跑的委托测试，实际上不会被跑。
"""

from __future__ import annotations

from typing import Any

import pytest

from dugentx.events import EVENT_MODES
from dugentx.kernel.context import Context
from dugentx.kernel.errors import PluginError
from dugentx.kernel.events import EventBus
from dugentx.plugins.subagent import create as create_subagent
from dugentx.seams.agent import Agent, AgentConfig, AgentRegistry, TurnResult
from dugentx.seams.messages import ToolCall, Usage
from dugentx.seams.session import SessionLog
from dugentx.seams.subagent import SubagentRequest
from dugentx.seams.tools import ToolRegistry
from dugentx.tools.subagent_tools import create as create_subagent_tools
from dugentx.tools.subagent_tools import register as register_subagent_tool

CHILD_TEXT = "子 Agent 的结论：顺序问题在 loader.py 的第 12 行。"


class _RecordingDriver:
    """不碰模型的 driver：把一段固定结论记进**子会话**。

    它顺手把拿到的 agent 记下来，于是「provider 有没有把步数和工具白名单
    收窄」变成可观测的事实，而不是靠读代码去相信。
    """

    def __init__(self, text: str = CHILD_TEXT) -> None:
        self.text = text
        self.seen: list[AgentConfig] = []
        self.prompts: list[str] = []

    async def run_turn(self, agent: Agent, prompt: str) -> TurnResult:
        self.seen.append(agent.config)
        self.prompts.append(prompt)
        agent.session.append("user/message", content=prompt)
        agent.session.append("assistant/message", content=self.text)
        return TurnResult(
            text=self.text,
            steps=2,
            usage=Usage(prompt_tokens=30, completion_tokens=12, total_tokens=42),
        )


class _BrokenDriver:
    """子 Agent 的第一步就炸。委托必须把这件事变成结论，而不是异常。"""

    async def run_turn(self, agent: Agent, prompt: str) -> TurnResult:
        raise RuntimeError("模型网关 502")


class _Store:
    """一个假会话存储：只记下谁被存过。"""

    def __init__(self) -> None:
        self.saved: list[str] = []

    def save(self, log: SessionLog) -> None:
        self.saved.append(log.session_id)


def _root(log: SessionLog | None = None, driver: Any = None) -> Context:
    ctx = Context("root", events=EventBus(EVENT_MODES))
    ctx.provide("session", log if log is not None else SessionLog("s-main"))
    ctx.provide("agents", AgentRegistry(ctx))
    ctx.provide("agentLoop", driver if driver is not None else _RecordingDriver())
    return ctx


def _kinds(log: SessionLog) -> list[str]:
    return [event.kind for event in log.events()]


# ------------------------------------------------------------------ 委托


async def test_the_child_gets_its_own_session_and_the_parent_log_gains_nothing() -> None:
    parent = SessionLog("s-main")
    parent.append("user/message", content="主会话原来就有的那句话。")
    ctx = _root(parent)
    dispose = await ctx.mount(create_subagent({"max_steps": 4}))

    seen: list[str] = []
    ctx.on("subagent/start", lambda request: seen.append(f"start:{request.label}"))
    ctx.on("subagent/end", lambda result: seen.append(f"end:{result.stop_reason}"))

    result = await ctx.service("subagents").spawn(
        ctx, SubagentRequest(prompt="把 loader.py 的导入顺序理一遍", label="tidy")
    )

    assert result.output == CHILD_TEXT
    assert result.session_id
    assert result.session_id != parent.session_id
    assert result.steps == 2
    assert result.usage.total_tokens == 42
    assert seen == ["start:tidy", "end:completed"]

    # 这个缝的全部意义就在这两行：父会话的日志一条都没多，
    # 子 Agent 说的每一句话都不在主会话的上下文里。
    assert _kinds(parent) == ["user/message"]
    assert CHILD_TEXT not in "".join(str(event.data) for event in parent.events())

    # 跑完就摘掉：名单上留着它，长会话就会攒出一堆僵尸 agent。
    assert ctx.service("agents").get(result.session_id) is None

    dispose()
    assert not ctx.has("subagents")


async def test_the_child_session_is_persisted_even_though_it_is_not_in_the_parent() -> None:
    """过程不进父上下文，但不是「没有记录」——它落在自己的会话文件里。"""
    store = _Store()
    ctx = _root()
    ctx.provide("sessionStore", store)
    await ctx.mount(create_subagent({}))

    result = await ctx.service("subagents").spawn(
        ctx, SubagentRequest(prompt="看一眼", label="peek")
    )
    assert store.saved == [result.session_id]


async def test_max_steps_and_tool_allow_are_narrowed_never_widened() -> None:
    driver = _RecordingDriver()
    ctx = _root(driver=driver)
    await ctx.mount(
        create_subagent(
            {"default_model": "small-model", "max_steps": 3, "tool_allow": ["read_file", "grep"]}
        )
    )

    await ctx.service("subagents").spawn(
        ctx,
        SubagentRequest(
            prompt="只读地看一眼", max_steps=99, tool_allow=("run_command", "read_file")
        ),
    )

    config = driver.seen[0]
    assert config.max_steps == 3  # provider 的上限说了算，请求不能把它放大
    assert config.tool_allow == ("read_file",)
    assert config.model == "small-model"
    assert config.label == "sub"


async def test_a_child_failure_comes_back_as_a_conclusion_instead_of_an_exception() -> None:
    """子 Agent 崩了不该把父会话一起带走：它应该变成一条可读的结论。"""
    ctx = _root(driver=_BrokenDriver())
    await ctx.mount(create_subagent({}))
    errors: list[str] = []
    ctx.on("agent/error", lambda where, error: errors.append(f"{where}: {error}"))

    result = await ctx.service("subagents").spawn(ctx, SubagentRequest(prompt="随便", label="boom"))

    assert result.stop_reason == "error"
    assert "502" in result.output
    assert errors == ["subagent:boom: 模型网关 502"]


# ------------------------------------------------------------------ 工具


async def test_delegate_task_returns_the_rendered_result() -> None:
    ctx = _root()
    ctx.provide("tools", ToolRegistry(ctx))
    await ctx.mount(create_subagent({}))
    dispose = register_subagent_tool(ctx)

    outcome = await ctx.service("tools").execute(
        ToolCall(
            id="c1",
            name="delegate_task",
            arguments={"prompt": "把 config.py 读一遍，告诉我加载顺序", "label": "cfg"},
        )
    )

    assert outcome.ok
    assert CHILD_TEXT in outcome.content
    # 过程留在子会话里，所以「用了多少步」这行交代是父会话唯一能判断可信度的东西。
    assert "用了 2 步" in outcome.content

    dispose()
    assert ctx.service("tools").names() == []


async def test_the_tool_plugin_registers_and_unwinds_cleanly() -> None:
    ctx = _root()
    ctx.provide("tools", ToolRegistry(ctx))
    await ctx.mount(create_subagent({}))

    dispose = await ctx.mount(create_subagent_tools({}))
    assert ctx.service("tools").names() == ["delegate_task"]
    tool = ctx.service("tools").get("delegate_task")
    assert tool.labels == frozenset({"write"})  # 它会以你的名义动这个世界

    dispose()
    assert ctx.service("tools").names() == []


async def test_configuration_typos_fail_at_load_time() -> None:
    ctx = _root()
    with pytest.raises(PluginError):
        await ctx.mount(create_subagent({"max_step": 3}))
    with pytest.raises(PluginError):
        await ctx.mount(create_subagent({"max_steps": 0}))
