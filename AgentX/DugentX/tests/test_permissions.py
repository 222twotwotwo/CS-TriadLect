"""permissions 缝的测试 —— 分级、拦、问、放行，以及卸载之后门要消失。

这里用真的 `ToolRegistry` 和真的工具，因为权限本来就是「装到管道上的东西」：
只测 `level_for` 的话，最该被验证的那一步（拦下来之后模型看到的是什么）
完全没有被覆盖。
"""

from __future__ import annotations

import io
from typing import Any

import pytest

from dugentx.kernel.context import Context
from dugentx.kernel.errors import PluginError
from dugentx.kernel.loader import resolve_factory
from dugentx.providers.approval_interactive import InteractiveApproval
from dugentx.providers.approval_policy import ArgumentRule, PolicyApproval
from dugentx.seams.messages import ToolCall
from dugentx.seams.permissions import ApprovalRequest, PermissionPolicy, install_gate
from dugentx.seams.tools import (
    LABEL_DANGEROUS,
    LABEL_READ,
    LABEL_WRITE,
    Tool,
    ToolRegistry,
    tool_from_function,
)


def _tool(name: str, labels: frozenset[str], ran: list[str]) -> Tool:
    """造一个真的工具：被调用时把名字记下来，这样「有没有真的跑」是可断言的。"""

    def handler(text: str = "") -> str:
        """做一件事。

        Args:
            text: 随便一段文本
        """
        ran.append(text or name)
        return f"{name}-done:{text}"

    return tool_from_function(handler, name=name, labels=labels)


def _never_ask(_prompt: str) -> str:
    raise AssertionError("没有终端就根本不该发问")


@pytest.fixture
def rig(ctx: Context) -> tuple[Context, list[str]]:
    """一个装了三个工具（只读 / 写 / 危险）的上下文，和一个「跑过什么」的记录。"""
    ran: list[str] = []
    registry = ToolRegistry(ctx)
    ctx.provide("tools", registry)
    for name, labels in (
        ("read_thing", frozenset({LABEL_READ})),
        ("write_thing", frozenset({LABEL_WRITE})),
        ("danger_thing", frozenset({LABEL_DANGEROUS})),
    ):
        registry.register(_tool(name, labels, ran))
    return ctx, ran


def _request(
    tool: str = "write_thing", level: str = "confirm", **arguments: Any
) -> ApprovalRequest:
    return ApprovalRequest(
        tool=tool,
        labels=frozenset({LABEL_WRITE}),
        arguments=arguments,
        level=level,  # type: ignore[arg-type]
    )


async def _run(ctx: Context, name: str, **arguments: Any) -> Any:
    return await ctx.service("tools").execute(ToolCall(id="c1", name=name, arguments=arguments))


# ------------------------------------------------------------------ 分级


def test_level_for_picks_the_strictest_label() -> None:
    policy = PermissionPolicy()
    assert policy.level_for(frozenset({LABEL_READ})) == "auto"
    assert policy.level_for(frozenset({LABEL_READ, LABEL_WRITE})) == "confirm"
    assert policy.level_for(frozenset({LABEL_WRITE, LABEL_DANGEROUS})) == "deny"
    # 没有标签的工具按 default 算，而不是按「最宽松」算。
    assert policy.level_for(frozenset()) == "confirm"
    assert policy.level_for(frozenset({"从未见过的标签"})) == "confirm"


def test_configured_levels_win_over_defaults() -> None:
    policy = PermissionPolicy()
    policy.levels[LABEL_WRITE] = "auto"
    assert policy.level_for(frozenset({LABEL_WRITE})) == "auto"
    assert policy.level_for(frozenset({LABEL_WRITE, LABEL_DANGEROUS})) == "deny"


# ------------------------------------------------------------------ 拦


async def test_deny_level_blocks_the_call(rig: tuple[Context, list[str]]) -> None:
    ctx, ran = rig
    install_gate(ctx, policy=PermissionPolicy(), approval=PolicyApproval())

    outcome = await _run(ctx, "danger_thing")
    assert outcome.blocked is True
    assert outcome.ok is False
    assert "deny" in outcome.content or "拒绝" in outcome.content
    assert ran == [], "被拦下的调用不该真的执行"


async def test_confirm_refused_by_policy_approval_blocks_too(
    rig: tuple[Context, list[str]],
) -> None:
    ctx, ran = rig
    install_gate(ctx, policy=PermissionPolicy(), approval=PolicyApproval())

    outcome = await _run(ctx, "write_thing", text="x")
    assert outcome.blocked is True
    assert outcome.ok is False
    assert "confirm" in outcome.content
    assert ran == []
    assert outcome.to_text().startswith("[已拦截]")


async def test_auto_level_passes_through_and_executes(rig: tuple[Context, list[str]]) -> None:
    ctx, ran = rig
    install_gate(ctx, policy=PermissionPolicy(), approval=PolicyApproval())

    outcome = await _run(ctx, "read_thing")
    assert outcome.ok is True
    assert outcome.blocked is False
    assert outcome.content == "read_thing-done:"
    assert ran == ["read_thing"]


# ------------------------------------------------------------- 策略审批本身


async def test_policy_approval_answers_every_level() -> None:
    approval = PolicyApproval(answers={"auto": True, "confirm": True, "deny": False})
    assert (await approval.decide(_request(level="confirm"))).allowed is True
    assert (await approval.decide(_request(level="deny"))).allowed is False


async def test_policy_approval_denies_unconfigured_levels() -> None:
    approval = PolicyApproval(answers={"auto": True})
    decision = await approval.decide(_request(level="confirm"))
    assert decision.allowed is False
    assert "confirm" in decision.reason


async def test_tool_override_beats_the_level_answer() -> None:
    approval = PolicyApproval(answers={"confirm": False}, tool_overrides={"write_thing": True})
    assert (await approval.decide(_request(tool="write_thing"))).allowed is True
    assert (await approval.decide(_request(tool="other_thing"))).allowed is False


async def test_argument_rule_decides_by_command_text() -> None:
    """同一条命令工具，命令文本不同，结论不同——这是 run_command 的标签做不到的事。"""
    approval = PolicyApproval(
        answers={"confirm": True},
        argument_rules=(
            ArgumentRule(
                tool="run_command", contains="rm -rf", allowed=False, reason="不许递归删根"
            ),
        ),
    )
    refused = await approval.decide(_request(tool="run_command", command="sudo rm -rf /tmp/x"))
    assert refused.allowed is False
    assert "不许递归删根" in refused.reason

    allowed = await approval.decide(_request(tool="run_command", command="ls -la"))
    assert allowed.allowed is True


async def test_argument_rule_can_be_scoped_to_one_tool() -> None:
    approval = PolicyApproval(
        answers={"confirm": True},
        argument_rules=(ArgumentRule(tool="run_command", contains="secret", allowed=False),),
    )
    assert (await approval.decide(_request(tool="other", text="secret"))).allowed is True
    assert (await approval.decide(_request(tool="run_command", text="secret"))).allowed is False


# ------------------------------------------------------------- 交互式审批


class _FakeTTY(io.StringIO):
    """一个自称是终端的输入流。只有「是不是终端」这一点被用到。"""

    def isatty(self) -> bool:
        return True


async def test_interactive_refuses_everything_without_a_tty() -> None:
    approval = InteractiveApproval(stream=io.StringIO(), ask=_never_ask)

    for level in ("auto", "confirm", "deny"):
        decision = await approval.decide(_request(level=level))
        assert decision.allowed is False
        assert "终端" in decision.reason


async def test_interactive_yes_allows_once() -> None:
    approval = InteractiveApproval(stream=_FakeTTY(), ask=lambda prompt: "y")
    assert (await approval.decide(_request())).allowed is True
    # `y` 只放行这一次：本会话的放行名单里不该有它。
    assert approval.allowed_tools == frozenset()


async def test_interactive_always_remembers_for_the_session() -> None:
    prompts: list[str] = []

    def ask(prompt: str) -> str:
        prompts.append(prompt)
        return "a"

    approval = InteractiveApproval(stream=_FakeTTY(), ask=ask)
    assert (await approval.decide(_request())).allowed is True
    assert approval.allowed_tools == frozenset({"write_thing"})
    assert len(prompts) == 1

    # 第二次不再问人：提问函数只被调用过一次。
    second = await approval.decide(_request())
    assert second.allowed is True
    assert "本会话" in second.reason
    assert len(prompts) == 1


async def test_interactive_no_and_junk_refuse() -> None:
    approval = InteractiveApproval(stream=_FakeTTY(), ask=lambda prompt: "n")
    assert (await approval.decide(_request())).allowed is False

    approval = InteractiveApproval(stream=_FakeTTY(), ask=lambda prompt: "随便")
    decision = await approval.decide(_request())
    assert decision.allowed is False
    assert "随便" in decision.reason


async def test_interactive_treats_eof_as_refusal() -> None:
    def eof(_prompt: str) -> str:
        raise EOFError

    approval = InteractiveApproval(stream=_FakeTTY(), ask=eof)
    assert (await approval.decide(_request())).allowed is False


# ------------------------------------------------------------------ 插件


async def test_permissions_plugin_wires_policy_approval_and_gate(ctx: Context) -> None:
    await ctx.mount(resolve_factory("dugentx.plugins.tools")({}))
    ctx.service("tools").register(_tool("write_thing", frozenset({LABEL_WRITE}), []))

    # answers 里写的是配置文档里的词（allow / refuse），不是 true / false。
    dispose = await ctx.mount(
        resolve_factory("dugentx.plugins.permissions")(
            {"mode": "policy", "answers": {"auto": "allow", "confirm": "allow", "deny": "refuse"}}
        )
    )

    assert isinstance(ctx.service("permissions"), PermissionPolicy)
    assert isinstance(ctx.service("approval"), PolicyApproval)
    outcome = await _run(ctx, "write_thing", text="ok")
    assert outcome.ok is True
    assert outcome.content == "write_thing-done:ok"

    # 拔掉插件：门、策略、审批者一起消失——这正是「每一笔注册都走 ctx.on/provide」
    # 换来的东西。
    dispose()
    assert ctx.get("permissions") is None
    assert ctx.get("approval") is None
    assert ctx.events.listeners("tools/pre-execute") == []
    assert (await _run(ctx, "write_thing", text="after")).ok is True


async def test_permissions_plugin_rejects_a_bad_level(ctx: Context) -> None:
    await ctx.mount(resolve_factory("dugentx.plugins.tools")({}))
    with pytest.raises(PluginError):
        await ctx.mount(
            resolve_factory("dugentx.plugins.permissions")({"levels": {"write": "warn"}})
        )


async def test_permissions_plugin_rejects_an_unknown_answer_word(ctx: Context) -> None:
    """`bool("refuse")` 是 True —— 所以认不出来的词必须装载失败，不能猜。"""
    await ctx.mount(resolve_factory("dugentx.plugins.tools")({}))
    with pytest.raises(PluginError):
        await ctx.mount(
            resolve_factory("dugentx.plugins.permissions")({"answers": {"confirm": "alow"}})
        )


async def test_permissions_plugin_refuses_deny_but_allows_confirm(ctx: Context) -> None:
    """一份写出来的配置，逐档验一遍结论——这是配置文件里最容易写反的地方。"""
    await ctx.mount(resolve_factory("dugentx.plugins.tools")({}))
    ran: list[str] = []
    registry = ctx.service("tools")
    for name, labels in (
        ("write_thing", frozenset({LABEL_WRITE})),
        ("danger_thing", frozenset({LABEL_DANGEROUS})),
    ):
        registry.register(_tool(name, labels, ran))

    await ctx.mount(
        resolve_factory("dugentx.plugins.permissions")(
            {
                "mode": "policy",
                "levels": {"dangerous": "deny"},
                "answers": {"auto": "allow", "confirm": "allow", "deny": "refuse"},
            }
        )
    )

    assert (await _run(ctx, "write_thing", text="ok")).ok is True
    blocked = await _run(ctx, "danger_thing")
    assert blocked.blocked is True
    assert ran == ["ok"], "被 deny 档拦下的调用不该执行"
