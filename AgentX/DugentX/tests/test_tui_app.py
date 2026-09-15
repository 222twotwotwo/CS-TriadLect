"""TUI 的端到端测试：从真实的组合配置里长出来，跑一个真回合。

这组测试要证明的是一句话：**加一个编码 TUI 没有改 harness。**
所以它不手工拼上下文，而是和用户一样从 `dugentx.tui.yml` 读配置、
`AgentRuntime.boot()` 起来，然后驱动界面。

两条硬约束：

- **不联网、不需要 key。** 模型用回放适配器（改一行 override），
  其余（工具、权限、会话、渲染）全部照常。
- **不需要真终端。** 非交互那条路（`--once`）直接读标准输出；
  交互那条路用 textual 自己的无头测试台（`App.run_test()` + `Pilot`）。
  一个只能在真终端上验证的界面，等于一个没验证过的界面。
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path
from typing import Any

import pytest

from dugentx.providers.model_switch import ModelChoice
from dugentx.runtime import AgentRuntime
from dugentx.seams.agent import TurnResult
from dugentx.seams.human import ALWAYS, APPROVAL_CHOICES, NO, Question
from dugentx.seams.messages import Usage
from dugentx.seams.permissions import ApprovalRequest
from dugentx.tui import commands
from dugentx.tui.formatting import SPINNER_FRAMES, StatusUpdate
from dugentx.tui.plain import PlainTui

ROOT = Path(__file__).resolve().parent.parent
TUI_CONFIG = ROOT / "dugentx.tui.yml"
WORKSPACE = ROOT / "examples" / "workspace"

ALLOW_ALL = {"mode": "policy", "answers": {"confirm": "allow"}}


def replay_overrides(turns: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """把 llm 那一行换成回放适配器——这是测试里唯一被改的东西。

    真实配置指向 DeepSeek 并只读 `DEEPSEEK_API_KEY`；测试里换掉适配器，
    其余（工具、权限、会话、渲染）全部照常，这才是端到端的意义。
    """
    return [
        {
            "id": "llm",
            "config": {
                "adapter": "replay",
                "model": "replay",
                "replay": {"on_exhausted": "error", "turns": turns},
            },
        }
    ]


def boot(
    turns: list[list[dict[str, Any]]],
    *,
    extra: list[dict[str, Any]] | None = None,
) -> AgentRuntime:
    return AgentRuntime.boot(
        TUI_CONFIG,
        cwd=ROOT,
        overrides=[*replay_overrides(turns), *(extra or [])],
    )


def require_textual() -> None:
    """textual 是可选依赖：没装就跳过需要界面的测试，而不是让整个文件红。

    核心的 `dugentx run` / `plugins` 不欠界面任何东西，所以那些测试不带这个前提；
    带前提的只有「真的把界面起来」的那些。
    """
    pytest.importorskip("textual")


def block_textual(monkeypatch: pytest.MonkeyPatch) -> None:
    """把 textual 变成「这台机器上没装」。

    `sys.modules[name] = None` 是让 `import name` 当场抛 ImportError 的标准写法。
    界面模块也要一起挡掉——它已经在缓存里的话，导入它不会再碰 textual，
    那样就测不到「没有界面库」这条路了。
    """
    monkeypatch.setitem(sys.modules, "textual", None)
    monkeypatch.setitem(sys.modules, "dugentx.tui.app", None)


# ------------------------------------------------------------------ 界面小工具


def transcript(app: Any) -> str:
    """画面上那本流水账的纯文本。断言的是**看到的东西**，不是内部状态。"""
    from textual.widgets import RichLog

    log = app.query_one("#transcript", RichLog)
    return "\n".join(strip.text for strip in log.lines)


async def submit(app: Any, pilot: Any, line: str) -> None:
    """把一行打进输入框并回车——和用户做的是同一件事。"""
    from textual.widgets import Input

    app.query_one("#prompt", Input).value = line
    await pilot.press("enter")
    await pilot.pause()


async def settle(pilot: Any, predicate: Any, *, what: str) -> None:
    """等一件事在界面上真的发生。

    焦点、屏幕切换、控件内容都是**消息**，`pilot.pause()` 只保证把手上这一批
    处理完，不保证别的东西已经到了。所以「等到它发生」比「pause 一次就断言」
    稳，失败时说的话也更有用：它告诉你等的是什么，而不是一句 `assert None`。
    """
    for _ in range(200):
        if predicate():
            return
        await pilot.pause()
    raise AssertionError(f"等不到：{what}")


def widgets() -> tuple[Any, Any, Any, Any]:
    """界面里会用到的几个类。放在函数里，是为了让没装 textual 时也能跑别的测试。"""
    from dugentx.tui.app import TextualHuman
    from dugentx.tui.widgets import ChoiceModal, ProviderPicker, StatusBar

    return TextualHuman, ChoiceModal, ProviderPicker, StatusBar


# ------------------------------------------------------------------ 组合


async def test_the_tui_config_swaps_only_the_human_channel() -> None:
    """整件事的证明：配置里只换了「谁在跟人说话」那一行。"""
    runtime = boot([[{"text": "好"}]])
    await runtime.start()
    try:
        ids = [row.id for row in runtime.rows()]
        assert "tui" in ids
        assert "human" not in ids, "stdio 通道应当被禁用，否则一个键会有两个提供者"

        assert runtime.ctx.has("human")
        assert runtime.ctx.has("tui")
        # TUI 通道和 stdio 通道实现的是同一条缝。
        assert runtime.ctx.service("human").name == "tui"
        # 其余一切都还在，而且没被动过。
        for service in ("llm", "tools", "fs", "shell", "permissions", "session", "prompt"):
            assert runtime.ctx.has(service), f"{service} 不该消失"
    finally:
        await runtime.stop()


async def test_without_a_terminal_nobody_can_answer_and_approval_refuses() -> None:
    """没有人能回答时，通道要说「取消」，审批要**拒绝**——不是挂住。

    这一条是这套设计最实际的收益：CI 里跑同一份配置不会卡死等一个永远不会来的按键。
    """
    runtime = boot([[{"text": "好"}]])
    await runtime.start()
    try:
        human = runtime.ctx.service("human")
        assert human.interactive() is False

        asked = await human.ask(Question(prompt="在吗？"))
        assert asked.cancelled is True
        assert asked.source == "tui"
        assert not asked, "没回答是假值；这和「回答了空字符串」不是一回事"

        chosen = await human.choose(
            Question(prompt="要写吗？", choices=APPROVAL_CHOICES, title="write_file")
        )
        assert chosen.cancelled is True
        assert not chosen

        request = ApprovalRequest(
            tool="write_file",
            labels=frozenset({"write"}),
            arguments={"path": "a.txt"},
            level="confirm",
        )
        decision = await runtime.ctx.service("approval").decide(request)
        assert decision.allowed is False
        assert "不是交互式环境" in decision.reason
    finally:
        await runtime.stop()


# ------------------------------------------------------------------ 非交互一条路


async def test_once_renders_a_real_turn_with_a_tool_a_result_and_a_diff(capsys) -> None:
    """跑一个真回合：模型流式吐字 → 请求工具 → 工具真执行 → 改文件 → diff → 收尾。

    断言的是**用户看到的东西**，不是内部状态。一个界面测试如果只断言
    「函数被调用了」，它对「界面到底长什么样」什么都没说。
    """
    target = WORKSPACE / "diff-demo.txt"
    target.write_text("第一行\n第二行\n", encoding="utf-8")
    turns = [
        [
            {"text": "我先读一下这个文件。\n"},
            {"tool_call": "read_file", "arguments": {"path": "examples/workspace/diff-demo.txt"}},
        ],
        [
            {"text": "读到了，我改第二行。\n"},
            {
                "tool_call": "edit_file",
                "arguments": {
                    "path": "examples/workspace/diff-demo.txt",
                    "old": "第二行",
                    "new": "第二行改过了",
                },
            },
        ],
        [{"text": "改完了。\n"}],
    ]
    runtime = boot(turns, extra=[{"id": "permissions", "config": ALLOW_ALL}])
    await runtime.start()
    try:
        code = await runtime.ctx.service("tui").run(once="把第二行改一下")
        assert code == 0
    finally:
        await runtime.stop()
        target.unlink(missing_ok=True)

    out = capsys.readouterr().out
    assert "• 把第二行改一下" in out, "用户的输入应当被回声"
    assert "▸ 我先读一下这个文件。" in out, "流式正文应当被拼成一行"
    assert "╭─ read_file" in out, "工具调用应当有可见的一行"
    assert "│ ✓ read_file" in out, "工具结果应当有可见的一行"
    assert "╭─ edit_file" in out
    assert "│ ✓ edit_file" in out
    # 定义性画面：改了文件，就要看见改了什么。
    assert "--- examples/workspace/diff-demo.txt" in out
    assert "-第二行" in out, f"diff 应当显示删掉的那行；实际输出：\n{out}"
    assert "+第二行改过了" in out, f"diff 应当显示新增的那行；实际输出：\n{out}"
    assert "── turn completed" in out
    assert "steps=3" in out
    assert "\x1b[" not in out, "非交互那条路只写纯文本"
    # 字形是排版（纯文本也在），动画不是（那个属于界面，见 StatusBar）。
    for frame in SPINNER_FRAMES:
        assert frame not in out, "非交互那条路里不该出现转圈的帧"


async def test_once_still_runs_when_textual_is_not_installed(capsys, monkeypatch) -> None:
    """硬要求：装了 tui 配置、没装 `--extra tui` 的机器上，`--once` 照样要跑。

    界面库是**可选**的：少一个依赖该少一层界面，不该少一次执行。
    """
    block_textual(monkeypatch)
    runtime = boot([[{"text": "好。\n"}]])
    await runtime.start()
    try:
        service = runtime.ctx.service("tui")
        assert isinstance(service, PlainTui), "没有 textual 时应当换成纯文本那条路"
        assert runtime.ctx.service("human").name == "tui"
        assert not runtime.ctx.service("tui").status_sink

        assert await service.run(once="你好") == 0
        out = capsys.readouterr().out
        assert "▸ 好。" in out
        assert "── turn completed" in out
    finally:
        await runtime.stop()


async def test_interactive_without_textual_says_exactly_what_to_install(
    capsys, monkeypatch
) -> None:
    """没有界面库时，交互模式只欠一句话：装什么、怎么装。绝对不是 traceback。"""
    block_textual(monkeypatch)
    runtime = boot([[{"text": "x"}]])
    await runtime.start()
    try:
        code = await runtime.ctx.service("tui").run()
    finally:
        await runtime.stop()

    assert code != 0
    err = capsys.readouterr().err.strip()
    assert "uv sync --extra tui" in err
    assert "ImportError" not in err
    assert "Traceback" not in err
    assert len(err.splitlines()) == 1, f"只说一句就够：{err!r}"


# ------------------------------------------------------------------ 界面本身


async def test_the_app_comes_up_with_a_transcript_a_prompt_and_a_status_bar() -> None:
    require_textual()
    runtime = boot([[{"text": "好"}]])
    await runtime.start()
    try:
        app = runtime.ctx.service("tui")
        _, _, _, StatusBar = widgets()
        async with app.run_test(size=(120, 40)) as pilot:
            from textual.widgets import Input

            assert "DugentX" in transcript(app), "开场那几行应当画出来"
            assert app.query_one("#prompt", Input) is not None
            bar = app.query_one("#status", StatusBar)
            assert bar.state["session_id"] == runtime.agent.session.session_id
            assert bar.state["permission"], "权限档位读得到 ctx.permissions 就该显示出来"
            assert app.focused is not None and app.focused.id == "prompt", "焦点该在输入框上"
            await pilot.pause()
    finally:
        await runtime.stop()


async def test_a_slash_command_typed_into_the_input_is_run_by_the_app() -> None:
    """命令是界面自己的，**不发给模型**——这条区分必须硬。"""
    require_textual()
    runtime = boot([[{"text": "好"}]])
    await runtime.start()
    try:
        app = runtime.ctx.service("tui")
        async with app.run_test(size=(120, 40)) as pilot:
            await submit(app, pilot, "/model")
            text = transcript(app)
            assert f"当前模型：{runtime.ctx.service('models').current().describe()}" in text

            await submit(app, pilot, "/status")
            text = transcript(app)
            assert "session=" in text
            assert "steps=" in text
            assert "tokens=" in text
            assert "权限：" in text

            await submit(app, pilot, "/help")
            text = transcript(app)
            for command in ("/model", "/provider", "/clear", "/quit"):
                assert command in text
            assert "不会发给模型" in text

            await submit(app, pilot, "/nope")
            text = transcript(app)
            assert "没有这条命令" in text
            assert "/help" in text

            # 一条命令都不该变成模型看到的提示词。
            assert runtime.agent.session.of_kind("user/message") == []
    finally:
        await runtime.stop()


async def test_a_prompt_typed_into_the_input_runs_a_real_turn() -> None:
    """交互那条路也要跑通一个真回合：流式正文、工具卡、**diff**、最终答复。

    diff 尤其要在这里看一次：它是这个界面存在的理由，而它来自「执行前后各读一遍
    文件」，不是从工具输出里解析出来的文字——两条路（这里和 `--once`）都得看得见。
    """
    require_textual()
    target = WORKSPACE / "diff-demo.txt"
    target.write_text("第一行\n第二行\n", encoding="utf-8")
    turns = [
        [
            {"text": "我先看看。\n"},
            {"tool_call": "list_dir", "arguments": {"path": "."}},
        ],
        [
            {"text": "我改一下这一行。\n"},
            {
                "tool_call": "edit_file",
                "arguments": {
                    "path": "examples/workspace/diff-demo.txt",
                    "old": "第二行",
                    "new": "第二行改过了",
                },
            },
        ],
        [{"text": "看完了。\n"}],
    ]
    runtime = boot(turns, extra=[{"id": "permissions", "config": ALLOW_ALL}])
    await runtime.start()
    try:
        app = runtime.ctx.service("tui")
        async with app.run_test(size=(120, 40)) as pilot:
            await submit(app, pilot, "看看这个目录，顺手改一下第二行")
            await app.workers.wait_for_complete()
            await pilot.pause()

            text = transcript(app)
            assert "• 看看这个目录，顺手改一下第二行" in text
            assert "▸ 我先看看。" in text
            assert "╭─ list_dir" in text
            assert "│ ✓ list_dir" in text
            assert "╭─ edit_file" in text
            assert "--- examples/workspace/diff-demo.txt" in text
            assert "-第二行" in text, f"diff 应当显示删掉的那行；实际：\n{text}"
            assert "+第二行改过了" in text
            assert "▸ 看完了。" in text
            assert "── turn completed" in text
            assert target.read_text(encoding="utf-8") == "第一行\n第二行改过了\n"
    finally:
        await runtime.stop()
        target.unlink(missing_ok=True)


async def test_clear_empties_the_view_but_leaves_the_session_log_alone() -> None:
    """清屏清的是屏幕。会话日志是唯一真相，一次手滑不该抹掉它。"""
    require_textual()
    runtime = boot([[{"text": "好"}]])
    await runtime.start()
    try:
        app = runtime.ctx.service("tui")
        async with app.run_test(size=(120, 40)) as pilot:
            await submit(app, pilot, "/help")
            assert transcript(app)
            before = len(runtime.agent.session.events())

            await submit(app, pilot, "/clear")

            assert transcript(app) == ""
            assert len(runtime.agent.session.events()) >= before, "日志不该被清掉"
    finally:
        await runtime.stop()


async def test_quit_and_ctrl_d_leave_the_app() -> None:
    require_textual()
    runtime = boot([[{"text": "好"}]])
    await runtime.start()
    try:
        app = runtime.ctx.service("tui")
        async with app.run_test(size=(120, 40)) as pilot:
            await submit(app, pilot, "/quit")
            assert app.is_running is False
    finally:
        await runtime.stop()

    runtime = boot([[{"text": "好"}]])
    await runtime.start()
    try:
        app = runtime.ctx.service("tui")
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("ctrl+d")  # 空输入框上的 Ctrl-D = 退出
            await pilot.pause()
            assert app.is_running is False
    finally:
        await runtime.stop()


async def test_up_recalls_the_previous_input() -> None:
    require_textual()
    runtime = boot([[{"text": "好"}]])
    await runtime.start()
    try:
        app = runtime.ctx.service("tui")
        async with app.run_test(size=(120, 40)) as pilot:
            from textual.widgets import Input

            await submit(app, pilot, "/help")
            await pilot.press("up")
            await pilot.pause()
            assert app.query_one("#prompt", Input).value == "/help"
            await pilot.press("down")
            await pilot.pause()
            assert app.query_one("#prompt", Input).value == ""
    finally:
        await runtime.stop()


async def test_the_same_commands_are_reachable_from_the_command_palette() -> None:
    """Ctrl-P 的面板和手打命令走的是同一条 `_run_command`——两条路，一份实现。"""
    require_textual()
    runtime = boot([[{"text": "好"}]])
    await runtime.start()
    try:
        app = runtime.ctx.service("tui")
        _, _, ProviderPicker, _ = widgets()
        async with app.run_test(size=(120, 40)) as pilot:
            titles = [command.title for command in app.get_system_commands(app.screen)]
            for name in ("/help", "/status", "/model", "/provider", "/clear", "/quit"):
                assert any(title.startswith(name) for title in titles), f"面板里少了 {name}"

            entry = next(
                c for c in app.get_system_commands(app.screen) if c.title.startswith("/provider")
            )
            # 面板就是这么调命令的：同步就同步调，返回 awaitable 才等它。
            result = entry.callback()
            if inspect.isawaitable(result):
                await result
            await settle(
                pilot, lambda: isinstance(app.screen, ProviderPicker), what="面板打开选择器"
            )
    finally:
        await runtime.stop()


async def test_the_provider_picker_opens_from_the_command_the_binding_and_the_palette() -> None:
    require_textual()
    runtime = boot([[{"text": "好"}]])
    await runtime.start()
    try:
        app = runtime.ctx.service("tui")
        _, _, ProviderPicker, _ = widgets()
        async with app.run_test(size=(120, 40)) as pilot:
            await submit(app, pilot, "/provider")
            def picker_open() -> bool:
                return isinstance(app.screen, ProviderPicker)

            def picker_closed() -> bool:
                return not picker_open()

            await settle(pilot, picker_open, what="/provider 打开选择器")
            await pilot.press("escape")
            await settle(pilot, picker_closed, what="Esc 关掉选择器")

            await pilot.press("ctrl+o")
            await settle(pilot, picker_open, what="Ctrl-O 打开选择器")
            await pilot.press("escape")
            await settle(pilot, picker_closed, what="Esc 关掉选择器")
    finally:
        await runtime.stop()


# ------------------------------------------------------------------ 状态条


async def test_the_status_bar_follows_events_instead_of_a_timer() -> None:
    """每个事件带着自己那半截事实来：模型名、tokens、步数。

    这几项**没有定时器**是有意的：一个每 200 毫秒重画一次的状态条会让你分不清
    「它变了」和「它只是又刷了一下」。会跳的只有转圈和秒表，见下面那条测试——
    那一半是钟的事实，不是编出来的。
    """
    require_textual()
    runtime = boot([[{"text": "好"}]])
    await runtime.start()
    try:
        app = runtime.ctx.service("tui")
        _, _, _, StatusBar = widgets()
        async with app.run_test(size=(120, 40)) as pilot:
            bar = app.query_one("#status", StatusBar)

            runtime.ctx.events.emit("model/switched", "openai", "gpt-4o-mini")
            await pilot.pause()
            assert bar.state["model"] == "openai/gpt-4o-mini"
            assert "openai/gpt-4o-mini" in str(bar.content)

            # 累计 tokens 从会话日志算：事件里那个数是这一轮的。
            runtime.agent.session.append(
                "assistant/message", content="x", total_tokens=1234, prompt_tokens=1000
            )
            runtime.ctx.events.emit("llm/usage", Usage(total_tokens=10))
            await pilot.pause()
            assert bar.state["tokens"] == 1234, "状态条上的 tokens 是这个会话累计的"

            runtime.ctx.events.emit("step/start", 2)
            await pilot.pause()
            assert bar.state["steps"] == 3, "步数说的是**这个回合**走到了哪一步"
    finally:
        await runtime.stop()


async def test_the_status_bar_ticks_while_a_turn_is_in_flight_and_stops_after() -> None:
    """转圈和秒表是**钟**的事实，所以它们可以跳——但它们只在有活的时候跳。

    「正在跑」由事件开关（`turn/start` / `turn/end`），跳的那一部分由定时器画。
    两句话而不是一句话：事实归事件，时间归钟。回合结束之后表必须停——
    一个还在走的秒表，比一个不动的秒表更让人以为它还在干活。
    """
    require_textual()
    runtime = boot([[{"text": "好"}]])
    await runtime.start()
    try:
        app = runtime.ctx.service("tui")
        _, _, _, StatusBar = widgets()
        async with app.run_test(size=(120, 40)) as pilot:
            bar = app.query_one("#status", StatusBar)
            assert bar.busy is False, "没活干的时候不该在转"

            runtime.ctx.events.emit("turn/start", "在吗")
            await pilot.pause()
            first = str(bar.content)
            assert bar.busy is True
            assert first[0] in SPINNER_FRAMES, f"状态条最前面是转圈的帧：{first!r}"

            await pilot.pause(0.3)
            second = str(bar.content)
            assert second != first, "干活的时候它得真的在动"
            assert bar.elapsed > 0.2
            assert bar.state["steps"] == 0, "事实还是事实：回合刚开始，步数是 0"

            runtime.ctx.events.emit(
                "turn/end", TurnResult(steps=2, tool_calls=0, usage=Usage(total_tokens=7))
            )
            await pilot.pause()
            idle = str(bar.content)
            assert bar.busy is False
            assert idle.startswith("·"), "回合结束了，表就停"
            assert "steps=2" in idle, "事实照旧跟着事件来——停的只是表"

            await pilot.pause(0.3)
            assert str(bar.content) == idle, f"停下来之后不该再跳：{bar.content!r}"
    finally:
        await runtime.stop()


async def test_animation_can_be_switched_off_and_then_the_spinner_stands_still() -> None:
    """关掉动效之后，状态条给的是一个**静止**的字形，也不再有秒表。

    这条是为「不该有动画的地方」留的：非交互那条路连状态条都没有（往管道里写
    动画只是把日志弄脏），而真终端里也可能有人不想看见会动的东西。
    """
    require_textual()
    from dugentx.tui.app import TuiApp
    from dugentx.tui.plain import TuiConfig

    runtime = boot([[{"text": "好"}]])
    await runtime.start()
    try:
        app = TuiApp(runtime.ctx, config=TuiConfig(animate=False), tty=True)
        _, _, _, StatusBar = widgets()
        async with app.run_test(size=(120, 40)) as pilot:
            bar = app.query_one("#status", StatusBar)
            bar.update_status(StatusUpdate(busy=True))
            await pilot.pause()
            first = str(bar.content)
            assert first[0] == "⠿", f"静止的那一帧：{first!r}"
            assert bar.busy is True, "有没有活在干和转不转圈是两件事"

            await pilot.pause(0.3)
            assert str(bar.content) == first, "不该有东西在跳"
    finally:
        await runtime.stop()


# ------------------------------------------------------------------ 补全


def popup_of(app: Any) -> Any:
    """输入框上面那个候选列表。"""
    from dugentx.tui.widgets import SuggestionPopup

    return app.query_one("#suggestions", SuggestionPopup)


async def test_typing_a_slash_puts_the_commands_on_screen_right_away() -> None:
    """打 `/` 的那一刻就该看见命令——不是按了 Tab 才看得见。

    这是「自然触发」：`/` 是命令的起点，不用谁来告诉我们补全该开始了。
    """
    require_textual()
    runtime = boot([[{"text": "好"}]])
    await runtime.start()
    try:
        app = runtime.ctx.service("tui")
        async with app.run_test(size=(120, 40)) as pilot:
            popup = popup_of(app)
            assert popup.display is False, "还没打 `/` 的时候不该占着屏幕"

            await pilot.press("slash")
            await pilot.pause()
            assert popup.display is True
            assert next(item.label for item in popup.items()) == "/help"
            assert popup.highlighted is not None
            assert popup.highlighted.label == "/help", "第一条就是高亮的那条"

            # 每一行都带用法和一句说明：只给一个名字，人还得去猜它是干什么的。
            shown = "\n".join(popup.lines)
            for command in commands.COMMANDS:
                assert command.name in shown
                assert command.summary in shown
            assert "/exit" in shown, "别名也是能打的一种写法，它得自己占一行"
    finally:
        await runtime.stop()


async def test_the_candidates_narrow_as_you_type() -> None:
    require_textual()
    runtime = boot([[{"text": "好"}]])
    await runtime.start()
    try:
        app = runtime.ctx.service("tui")
        async with app.run_test(size=(120, 40)) as pilot:
            popup = popup_of(app)
            await pilot.press("slash", "s", "t")
            await pilot.pause()
            assert [item.label for item in popup.items()] == ["/status"]

            await pilot.press("backspace")
            await pilot.pause()
            assert [item.label for item in popup.items()] == ["/status"], (
                "`/s` 也只剩这一条：别的都不以 s 开头"
            )
    finally:
        await runtime.stop()


async def test_arrow_keys_move_the_highlight_and_tab_takes_it_without_running_it() -> None:
    """`↑`/`↓` 挑，`Tab` 接受但**不提交**——「补完再看一眼」是一个真需要的动作。"""
    require_textual()
    runtime = boot([[{"text": "好"}]])
    await runtime.start()
    try:
        app = runtime.ctx.service("tui")
        async with app.run_test(size=(120, 40)) as pilot:
            from textual.widgets import Input

            popup = popup_of(app)
            await pilot.press("slash")
            await pilot.pause()
            assert popup.highlighted.label == "/help"

            await pilot.press("down")
            await pilot.pause()
            assert popup.highlighted.label == "/status"
            await pilot.press("up")
            await pilot.pause()
            assert popup.highlighted.label == "/help"

            await pilot.press("tab")
            await pilot.pause()
            assert app.query_one("#prompt", Input).value == "/help "
            assert runtime.agent.session.of_kind("user/message") == [], "Tab 只补全，不跑命令"
    finally:
        await runtime.stop()


async def test_enter_takes_the_highlighted_command_and_runs_it() -> None:
    """补的是命令名时，回车顺手把这一行交出去：补完就等于打完了。"""
    require_textual()
    runtime = boot([[{"text": "好"}]])
    await runtime.start()
    try:
        app = runtime.ctx.service("tui")
        async with app.run_test(size=(120, 40)) as pilot:
            from textual.widgets import Input

            await pilot.press("slash", "s", "t", "a")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            assert app.query_one("#prompt", Input).value == "", "交出去之后输入框该空"
            text = transcript(app)
            assert "session=" in text and "tokens=" in text, f"/status 该跑起来：\n{text}"
            assert popup_of(app).display is False
            assert runtime.agent.session.of_kind("user/message") == [], (
                "命令是界面自己的，不发给模型"
            )
    finally:
        await runtime.stop()


async def test_escape_puts_the_list_away_and_keeps_what_was_typed() -> None:
    """Esc 是「我不要这个提示」，不是「我不要我刚打的这行字」。"""
    require_textual()
    runtime = boot([[{"text": "好"}]])
    await runtime.start()
    try:
        app = runtime.ctx.service("tui")
        async with app.run_test(size=(120, 40)) as pilot:
            from textual.widgets import Input

            await pilot.press("slash", "m", "o")
            await pilot.pause()
            assert popup_of(app).display is True

            await pilot.press("escape")
            await pilot.pause()
            assert popup_of(app).display is False
            assert app.query_one("#prompt", Input).value == "/mo", "字一个都不能少"
    finally:
        await runtime.stop()


async def test_enter_still_submits_when_there_is_nothing_to_complete() -> None:
    """最不能出错的那一条：一句普通的话，回车必须照常发出去。

    弹出和补全都是**加**上去的：它们一个字都不该改掉「回车 = 提交」这件事。
    """
    require_textual()
    runtime = boot([[{"text": "好。\n"}]])
    await runtime.start()
    try:
        app = runtime.ctx.service("tui")
        async with app.run_test(size=(120, 40)) as pilot:
            from textual.widgets import Input

            await pilot.press("h", "i")
            await pilot.pause()
            assert popup_of(app).display is False, "一句普通的话不该让弹出冒出来"

            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert app.query_one("#prompt", Input).value == ""
            assert [
                event.data["content"] for event in runtime.agent.session.of_kind("user/message")
            ] == ["hi"]
            assert "• hi" in transcript(app)
            assert "▸ 好。" in transcript(app)
    finally:
        await runtime.stop()


async def test_tab_is_not_swallowed_when_there_is_nothing_to_complete() -> None:
    """没有候选的时候，Tab 不是「界面的键」——它得落回别处，而不是消失。"""
    require_textual()
    runtime = boot([[{"text": "好"}]])
    await runtime.start()
    try:
        app = runtime.ctx.service("tui")
        async with app.run_test(size=(120, 40)) as pilot:
            from textual.widgets import Input

            await pilot.press("h", "i")
            await pilot.pause()
            assert app.check_action("complete", ()) is False, "这一行没有候选可补"
            assert app.check_action("accept_completion", ()) is False, "弹出也没开"

            await pilot.press("tab")
            await pilot.pause()
            assert app.query_one("#prompt", Input).value == "hi", "Tab 什么都没改"
    finally:
        await runtime.stop()


async def test_the_argument_of_model_is_completed_from_the_model_in_play() -> None:
    """`/model` 的参数补的是**现在这个模型名**——它是离线唯一知道的那一个。

    provider 名单不是模型名：把它填进去，命令会当成模型名去用（见
    `commands._argument_items` 里那段说明）。
    """
    require_textual()
    runtime = boot([[{"text": "好"}]])
    await runtime.start()
    try:
        app = runtime.ctx.service("tui")
        model = runtime.ctx.service("models").current().model
        async with app.run_test(size=(120, 40)) as pilot:
            from textual.widgets import Input

            popup = popup_of(app)
            await pilot.press("slash", "m", "o", "d", "e", "l", "space")
            await pilot.pause()
            assert [item.label for item in popup.items()] == [model]
            assert popup.completion is not None and popup.completion.mode == "argument"

            await pilot.press("tab")
            await pilot.pause()
            assert app.query_one("#prompt", Input).value == f"/model {model}"

            # 不认识的名字就不补：硬凑一个候选，等于把猜出来的值填给人。
            for _ in range(len(f"/model {model}")):
                await pilot.press("backspace")
            await pilot.press("z", "z", "z")
            await pilot.pause()
            assert popup.display is False
    finally:
        await runtime.stop()


async def test_the_providers_the_picker_lists_are_the_providers_it_completes() -> None:
    """补全和**选择器**必须同意，而不是和「同一个来源」同意。

    两份名单会长成两个「可选的东西」的答案，而人只会相信屏幕上后来出现的那个。
    所以这里把选择器真的打开，拿它的列表和候选一行行比。
    """
    require_textual()
    runtime = boot([[{"text": "好"}]])
    await runtime.start()
    try:
        app = runtime.ctx.service("tui")
        _, _, ProviderPicker, _ = widgets()
        async with app.run_test(size=(120, 40)) as pilot:
            popup = popup_of(app)
            await pilot.press("slash", "p", "r", "o", "v", "i", "d", "e", "r", "space")
            await pilot.pause()
            completed = [item.label for item in popup.items()]
            assert completed, "打完 `/provider ` 应该有候选"

            await pilot.press("escape")
            await pilot.press("backspace")
            await pilot.pause()

            await submit(app, pilot, "/provider")
            await settle(pilot, lambda: isinstance(app.screen, ProviderPicker), what="打开选择器")
            listed = [
                label
                for label in option_labels(app.screen)
                if not label.startswith("——")  # 分隔标题不是一个可选项
            ]
            assert completed == listed
    finally:
        await runtime.stop()


async def test_the_popup_window_follows_the_highlight() -> None:
    """候选比屏幕高的时候，窗口跟着高亮走，而且窗口是有上限的。

    一个「选中了第十九条、屏幕上是前八条」的列表，看起来就像按了没反应。
    """
    require_textual()
    runtime = boot([[{"text": "好"}]])
    await runtime.start()
    try:
        app = runtime.ctx.service("tui")
        async with app.run_test(size=(120, 40)) as pilot:
            popup = popup_of(app)
            items = tuple(
                commands.Suggestion(value=f"/cmd{index}", label=f"/cmd{index}")
                for index in range(20)
            )
            popup.show(commands.Completion(mode="command", start=0, query="", items=items))
            await pilot.pause()

            first_window = len(popup.rows())
            assert popup.rows()[0] is items[0], "还没动的时候从第一条开始"
            assert 0 < first_window < len(items), "整份列表不该一次全画出来"

            for _ in range(19):
                popup.move(1)
            await pilot.pause()
            assert popup.highlighted is items[-1]
            assert popup.rows()[-1] is items[-1], "高亮必须落在窗口里"
            assert len(popup.rows()) == first_window, "滚到头了窗口也不该缩"

            popup.move(1)
            assert popup.highlighted is items[0], "到头绕回第一条"
    finally:
        await runtime.stop()


async def test_an_answer_is_never_rewritten_by_completion() -> None:
    """有人正在等一句回答的时候，那一行是**回答**，不是命令。

    一个以 `/` 开头的回答会被候选表按命令来补，回车也会变成「接受候选」——
    于是人打的那句话被悄悄换成了别的东西，而回答这种东西是不能被改写的。
    """
    require_textual()
    runtime = boot([[{"text": "好"}]])
    await runtime.start()
    try:
        app = runtime.ctx.service("tui")
        TextualHuman, _, _, _ = widgets()
        async with app.run_test(size=(120, 40)) as pilot:
            human = TextualHuman(app, tty=True)
            worker = app.run_worker(human.ask(Question(prompt="想说什么？")))
            await settle(pilot, lambda: "想说什么？" in transcript(app), what="问题出现")

            await pilot.press("slash", "h", "e", "l", "p")
            await pilot.pause()
            assert popup_of(app).display is False, "回答不该被当成命令来补"
            assert app.check_action("accept_completion", ()) is False

            await pilot.press("enter")
            answer = await worker.wait()
            assert answer.text == "/help", "回答原样交出去"
            assert "◦ /help" in transcript(app)
    finally:
        await runtime.stop()


async def test_the_provider_command_opens_the_picker_with_what_was_completed() -> None:
    """补进 `/provider` 的那个词**得有作用**：它成了选择器的过滤词。

    一个补进去却被忽略的参数，比补不出来更容易让人以为界面坏了。
    """
    require_textual()
    runtime = boot([[{"text": "好"}]])
    await runtime.start()
    try:
        app = runtime.ctx.service("tui")
        _, _, ProviderPicker, _ = widgets()
        async with app.run_test(size=(120, 40)) as pilot:
            from textual.widgets import Input

            # 第一次回车补参数（参数补全不顺手提交），第二次回车才跑命令。
            await submit(app, pilot, "/provider mist")
            assert app.query_one("#prompt", Input).value == "/provider mistral"

            await pilot.press("enter")
            await settle(pilot, lambda: isinstance(app.screen, ProviderPicker), what="打开选择器")

            picker = app.screen
            assert picker.query_one("#picker-filter", Input).value == "mistral"
            assert option_labels(picker) == ["mistral"], "过滤词是从命令的参数来的"
    finally:
        await runtime.stop()


# ------------------------------------------------------------------ 选择器


class FakeModels:
    """一个假的 `ctx.models`：界面只会用到这五个方法，一个不多。

    用它而不是真的换 provider，是因为这一条要测的是**界面**：过滤、选中、
    把错误留在弹窗里。真去构造适配器会把测试绑在「这台机器上配了哪个 key」上，
    而那正是这套测试最不该有的前提。
    """

    def __init__(self) -> None:
        self.switched: list[tuple[str, str]] = []
        self.used: list[str] = []

    def providers(self) -> list[str]:
        return ["deepseek", "openai", "mistral", "qwen"]

    def adapters(self) -> list[str]:
        return ["replay"]

    def current(self) -> ModelChoice:
        return ModelChoice(provider="deepseek", model="deepseek-flash", adapter="anyllm")

    def switch(self, *, provider: str, model: str, **_: Any) -> ModelChoice:
        self.switched.append((provider, model))
        if provider == "openai":
            raise _plugin_error("openai 需要 OPENAI_API_KEY，环境里没有它")
        return ModelChoice(provider=provider, model=model, adapter="anyllm")

    def use(self, name: str) -> ModelChoice:
        self.used.append(name)
        return ModelChoice(provider="", model="replay", adapter=name)


def _plugin_error(text: str) -> Exception:
    """真的 `PluginError`，不是随手一个 Exception。

    界面接的是这个类型；用一个泛泛的异常去测，测到的是一段永远走不到的分支。
    """
    from dugentx.kernel.errors import PluginError

    return PluginError(text)


def option_labels(picker: Any) -> list[str]:
    from textual.widgets import OptionList

    listing = picker.query_one("#picker-options", OptionList)
    return [str(option.prompt) for option in listing.options]


async def open_picker(app: Any, pilot: Any, models: Any, on_pick: Any = None) -> Any:
    _, _, ProviderPicker, _ = widgets()

    picker = ProviderPicker(models, current=models.current())
    app.push_screen(picker, on_pick)
    await pilot.pause()
    return picker


async def test_the_picker_filters_as_you_type() -> None:
    require_textual()
    runtime = boot([[{"text": "好"}]])
    await runtime.start()
    try:
        app = runtime.ctx.service("tui")
        fake = FakeModels()
        async with app.run_test(size=(120, 40)) as pilot:
            from textual.widgets import Input

            picker = await open_picker(app, pilot, fake)
            assert "deepseek" in option_labels(picker)
            assert "replay" in option_labels(picker), "已经注册的适配器也要列出来"

            picker.query_one("#picker-filter", Input).value = "mi"
            await pilot.pause()
            assert option_labels(picker) == ["mistral"]

            # 子序列也能匹配：打 `ds` 找得到 `deepseek`。
            picker.query_one("#picker-filter", Input).value = "ds"
            await pilot.pause()
            assert option_labels(picker) == ["deepseek"]

            picker.query_one("#picker-filter", Input).value = "zzz"
            await pilot.pause()
            assert option_labels(picker) == ["没有匹配的名字"]
    finally:
        await runtime.stop()


async def test_choosing_a_provider_then_a_model_calls_switch() -> None:
    require_textual()
    runtime = boot([[{"text": "好"}]])
    await runtime.start()
    try:
        app = runtime.ctx.service("tui")
        fake = FakeModels()
        picked: list[Any] = []
        async with app.run_test(size=(120, 40)) as pilot:
            from textual.widgets import Input

            def on_pick(choice: Any) -> None:
                picked.append(choice)
                app.choose_model(choice)

            picker = await open_picker(app, pilot, fake, on_pick)

            picker.query_one("#picker-filter", Input).value = "mistral"
            await pilot.pause()
            await pilot.press("enter")  # 选中过滤后的第一条
            await settle(pilot, lambda: picker.mode == "model", what="选了 provider 之后问模型名")

            model_input = picker.query_one("#picker-model", Input)
            for char in "mistral-large":
                await pilot.press(char)
            assert model_input.value == "mistral-large"
            await pilot.press("enter")
            await settle(
                pilot,
                lambda: not isinstance(app.screen, type(picker)),
                what="选完关掉弹窗",
            )

            assert fake.switched == [("mistral", "mistral-large")]
            assert picked == [
                ModelChoice(provider="mistral", model="mistral-large", adapter="anyllm")
            ]
            assert "mistral-large" in str(app.query_one("#status").content)
    finally:
        await runtime.stop()


async def test_a_rejected_switch_is_shown_inside_the_picker_and_the_app_survives() -> None:
    """key 没配是**这一件事**没做成，不是这个界面坏了：错误留在弹窗里，弹窗不关。"""
    require_textual()
    runtime = boot([[{"text": "好"}]])
    await runtime.start()
    try:
        app = runtime.ctx.service("tui")
        fake = FakeModels()
        async with app.run_test(size=(120, 40)) as pilot:
            from textual.widgets import Input, Static

            picker = await open_picker(app, pilot, fake)
            picker.query_one("#picker-filter", Input).value = "openai"
            await pilot.pause()
            await pilot.press("enter")
            await settle(pilot, lambda: picker.mode == "model", what="问模型名")
            for char in "gpt-4o-mini":
                await pilot.press(char)
            await pilot.press("enter")
            await settle(
                pilot,
                lambda: "OPENAI_API_KEY" in str(picker.query_one("#picker-error", Static).content),
                what="把 PluginError 显示在弹窗里",
            )

            assert app.is_running is True, "一次换不成不该把界面带走"
            assert app.screen is picker, "弹窗要留着，让人改个名字再试"
            error = str(picker.query_one("#picker-error", Static).content)
            assert "OPENAI_API_KEY" in error
    finally:
        await runtime.stop()


async def test_the_picker_can_switch_back_to_a_registered_adapter() -> None:
    """「回得去」这件事全部实现就在这一段：没有它，换成真 provider 就回不到离线模式了。"""
    require_textual()
    runtime = boot([[{"text": "好"}]])
    await runtime.start()
    try:
        app = runtime.ctx.service("tui")
        fake = FakeModels()
        async with app.run_test(size=(120, 40)) as pilot:
            from textual.widgets import Input

            picker = await open_picker(app, pilot, fake)
            picker.query_one("#picker-filter", Input).value = "replay"
            await pilot.pause()
            assert option_labels(picker)[-1] == "replay"
            await pilot.press("enter")
            await settle(
                pilot, lambda: not isinstance(app.screen, type(picker)), what="选中适配器后关掉弹窗"
            )

            assert fake.used == ["replay"]
            assert not isinstance(app.screen, type(picker))
    finally:
        await runtime.stop()


async def test_escape_cancels_the_picker_and_changes_nothing() -> None:
    require_textual()
    runtime = boot([[{"text": "好"}]])
    await runtime.start()
    try:
        app = runtime.ctx.service("tui")
        fake = FakeModels()
        async with app.run_test(size=(120, 40)) as pilot:
            picker = await open_picker(app, pilot, fake)
            await pilot.press("escape")
            await settle(
                pilot, lambda: not isinstance(app.screen, type(picker)), what="Esc 关掉选择器"
            )

            assert fake.switched == []
            assert fake.used == []
    finally:
        await runtime.stop()


# ------------------------------------------------------------------ human 通道


async def test_the_approval_default_is_refuse() -> None:
    """回车通向最保守的答案：误拒的代价是再敲一次，误允的代价不是。"""
    require_textual()
    runtime = boot([[{"text": "好"}]])
    await runtime.start()
    try:
        app = runtime.ctx.service("tui")
        TextualHuman, ChoiceModal, _, _ = widgets()
        async with app.run_test(size=(120, 40)) as pilot:
            human = TextualHuman(app, tty=True)
            assert human.interactive() is True

            question = Question(
                prompt="要执行 edit_file 吗？",
                detail="edit_file(path='a.txt')",
                choices=APPROVAL_CHOICES,
                title="edit_file",
            )
            worker = app.run_worker(human.choose(question))
            await settle(pilot, lambda: isinstance(app.screen, ChoiceModal), what="审批弹窗出现")
            await settle(
                pilot,
                lambda: getattr(app.focused, "id", None) == "choice-n",
                what="焦点落在默认项（拒绝）上",
            )

            await pilot.press("enter")
            answer = await worker.wait()
            assert answer.text == NO
            assert answer.cancelled is False
    finally:
        await runtime.stop()


async def test_an_approval_remembers_allow_always_for_the_same_tool() -> None:
    """`a` 记住一整个会话，粒度是**工具名**（提问的标题）。

    每次都要再按一次 y，只会训练出不停按 y 的人，那时候这道门就只剩装饰作用了。
    """
    require_textual()
    runtime = boot([[{"text": "好"}]])
    await runtime.start()
    try:
        app = runtime.ctx.service("tui")
        TextualHuman, ChoiceModal, _, _ = widgets()
        async with app.run_test(size=(120, 40)) as pilot:
            human = TextualHuman(app, tty=True)
            question = Question(
                prompt="要执行 edit_file 吗？",
                detail="edit_file(path='a.txt')",
                choices=APPROVAL_CHOICES,
                title="edit_file",
            )
            worker = app.run_worker(human.choose(question))
            await settle(pilot, lambda: isinstance(app.screen, ChoiceModal), what="审批弹窗出现")
            await pilot.press("a")
            assert (await worker.wait()).text == ALWAYS
            assert human.remembered == frozenset({"edit_file"})

            # 同一个工具不再问第二遍——回答仍然是当时那个回答。
            again = await human.choose(question)
            assert again.text == ALWAYS
            assert again.cancelled is False

            # 另一个工具照问不误：粒度是工具名，不是「以后都别问了」。
            other = Question(prompt="要执行吗？", choices=APPROVAL_CHOICES, title="write_file")
            worker = app.run_worker(human.choose(other))
            await settle(
                pilot, lambda: isinstance(app.screen, ChoiceModal), what="换一个工具照样要问"
            )
            await pilot.press("n")
            assert (await worker.wait()).text == NO

            # 回答要在流水账里留下痕迹：不然事后没人说得清当时同意了哪一条。
            assert "◦ " in transcript(app)
    finally:
        await runtime.stop()


async def test_escape_on_an_approval_means_nobody_answered() -> None:
    require_textual()
    runtime = boot([[{"text": "好"}]])
    await runtime.start()
    try:
        app = runtime.ctx.service("tui")
        TextualHuman, ChoiceModal, _, _ = widgets()
        async with app.run_test(size=(120, 40)) as pilot:
            human = TextualHuman(app, tty=True)
            question = Question(prompt="要执行吗？", choices=APPROVAL_CHOICES, title="write_file")
            worker = app.run_worker(human.choose(question))
            await settle(pilot, lambda: isinstance(app.screen, ChoiceModal), what="审批弹窗出现")
            await pilot.press("escape")

            answer = await worker.wait()
            assert answer.cancelled is True
            assert not answer
            assert human.remembered == frozenset()
    finally:
        await runtime.stop()


async def test_ask_uses_the_input_box_and_records_the_answer() -> None:
    require_textual()
    runtime = boot([[{"text": "好"}]])
    await runtime.start()
    try:
        app = runtime.ctx.service("tui")
        TextualHuman, _, _, _ = widgets()
        async with app.run_test(size=(120, 40)) as pilot:
            human = TextualHuman(app, tty=True)
            worker = app.run_worker(human.ask(Question(prompt="你叫什么？", detail="随便答一个")))
            await settle(
                pilot, lambda: "你叫什么？" in transcript(app), what="问题出现在流水账里"
            )
            await pilot.press("小", "林", "enter")
            answer = await worker.wait()

            assert answer.text == "小林"
            assert answer.cancelled is False
            assert "◦ 小林" in transcript(app)
    finally:
        await runtime.stop()


async def test_escape_while_asking_cancels_the_question() -> None:
    require_textual()
    runtime = boot([[{"text": "好"}]])
    await runtime.start()
    try:
        app = runtime.ctx.service("tui")
        TextualHuman, _, _, _ = widgets()
        async with app.run_test(size=(120, 40)) as pilot:
            human = TextualHuman(app, tty=True)
            worker = app.run_worker(human.ask(Question(prompt="在吗？")))
            await settle(pilot, lambda: "在吗？" in transcript(app), what="问题出现在流水账里")
            await pilot.press("escape")

            answer = await worker.wait()
            assert answer.cancelled is True
            assert "（取消）" in transcript(app)
    finally:
        await runtime.stop()
