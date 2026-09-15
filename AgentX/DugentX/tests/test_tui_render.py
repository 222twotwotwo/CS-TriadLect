"""排版层的测试 —— 全部对着一个 StringIO 跑，不需要终端、不需要 textual。

这一层最容易「看起来对」：眼睛看不出「这里多打了一个换行」「两个 delta 变成了
两行」这类事。把输出收进字符串，这些就都成了可以断言的字符串。

非交互那条路（`--once`、CI、`examples/tui_demo.py`）用的就是这个渲染器，
所以这里的每条断言同时也是「脚本拿到的那份输出」的断言。
"""

from __future__ import annotations

import io
import re
from typing import Any

import pytest

from dugentx.seams.agent import TurnResult
from dugentx.seams.human import APPROVAL_CHOICES, Question
from dugentx.seams.messages import Usage
from dugentx.seams.tools import LABEL_WRITE, tool_from_function
from dugentx.tools.fs_tools import _TOOLS
from dugentx.tools.shell_tools import run_command
from dugentx.tui import commands
from dugentx.tui.formatting import (
    ANSWER_MARKER,
    ASSISTANT_MARKER,
    RESULT_HEAD_LINES,
    RESULT_TAIL_LINES,
    SPINNER_FRAMES,
    SPINNER_STATIC,
    USER_MARKER,
    WRITE_TOOLS,
    PlainRenderer,
    banner_lines,
    busy_text,
    classify,
    diff_lines,
    elapsed_text,
    error_line,
    notice_line,
    question_lines,
    spinner_frame,
    status_line,
    step_line,
    summarize_arguments,
    tool_call_line,
    tool_result_lines,
    turn_end_line,
    unified_diff,
)


def make() -> tuple[PlainRenderer, io.StringIO]:
    """一个渲染器，和一个能把输出读回来的流。"""
    stream = io.StringIO()
    return PlainRenderer(stream), stream


def rows(stream: io.StringIO) -> list[str]:
    return stream.getvalue().splitlines()


# ------------------------------------------------------------------ 流式正文


def test_two_deltas_then_end_is_exactly_one_line() -> None:
    renderer, out = make()
    renderer.assistant_delta("先写半个")
    renderer.assistant_delta("句子。")
    renderer.assistant_end()

    assert rows(out) == [ASSISTANT_MARKER + "先写半个句子。"]
    assert out.getvalue().endswith("\n")


def test_deltas_do_not_print_a_newline_until_end() -> None:
    renderer, out = make()
    renderer.assistant_delta("还没有结束")
    assert "\n" not in out.getvalue()


def test_assistant_end_is_idempotent() -> None:
    renderer, out = make()
    renderer.assistant_delta("一句话。")
    renderer.assistant_end()
    renderer.assistant_end()
    renderer.assistant_end()

    assert rows(out) == [ASSISTANT_MARKER + "一句话。"]


def test_assistant_end_without_any_text_prints_nothing() -> None:
    renderer, out = make()
    renderer.assistant_end()
    assert out.getvalue() == ""


def test_tool_call_closes_an_open_assistant_line() -> None:
    renderer, out = make()
    renderer.assistant_delta("我去看一眼。")
    renderer.tool_call("read_file", {"path": "a.py"})

    first, second = rows(out)
    assert first == ASSISTANT_MARKER + "我去看一眼。"
    assert second.startswith("╭─ read_file")


def test_the_plain_path_never_writes_an_escape_sequence() -> None:
    """把每个方法都跑一遍，输出里不该出现任何一个 ANSI 转义。

    这不是「为了测试」：这条路会进管道、进日志、进 CI 的记录，往里面塞
    ANSI 的后果是文件里多出一堆看不懂的字符。颜色是界面的事。
    """
    renderer, out = make()
    renderer.banner(model="gpt-x", session_id="sess-0123456789", cwd="C:/w", tools=3)
    renderer.user("改一下这个文件")
    renderer.assistant_delta("好")
    renderer.assistant_end()
    renderer.step(1)
    renderer.tool_call("read_file", {"path": "a.py", "max_lines": 50})
    renderer.tool_result(name="read_file", content="ok", ok=True, blocked=False)
    renderer.diff(path="a.py", before="one\n", after="two\n")
    renderer.notice("只是旁白", kind="dim")
    renderer.notice("小心", kind="warn")
    renderer.question(Question(prompt="执行吗？", choices=APPROVAL_CHOICES))
    renderer.answer("允许这一次")
    renderer.turn_end(TurnResult(steps=1, tool_calls=1, usage=Usage(total_tokens=9)))
    renderer.error("炸了")

    assert "\x1b[" not in out.getvalue()


def test_the_renderer_writes_only_to_its_own_stream(capsys: pytest.CaptureFixture[str]) -> None:
    renderer, out = make()
    renderer.user("你好")

    assert "你好" in out.getvalue()
    assert capsys.readouterr().out == ""


# ------------------------------------------------------------------ 写工具名单


def test_write_tools_are_the_file_writers_from_the_registry() -> None:
    """名字不抄一遍，从 fs_tools 真正注册的那张表里算出来。"""
    registered = {
        tool_from_function(fn, labels=labels).name
        for fn, labels in _TOOLS
        if LABEL_WRITE in labels
    }
    assert registered == {"write_file", "edit_file"}
    assert registered == WRITE_TOOLS


def test_a_write_labelled_shell_tool_is_not_diffable() -> None:
    """`run_command` 也带 write 标签，但它没有可对比的前后文本。"""
    name = tool_from_function(run_command, labels=frozenset({LABEL_WRITE})).name
    assert name == "run_command"
    assert name not in WRITE_TOOLS


# ---------------------------------------------------------------- unified_diff


def test_identical_text_produces_no_diff_at_all() -> None:
    assert unified_diff("a\nb\n", "a\nb\n", path="x.py") == ""
    assert unified_diff("", "", path="x.py") == ""


def test_diff_has_headers_hunks_and_markers() -> None:
    text = unified_diff("one\ntwo\n", "one\nthree\n", path="x.py")
    parts = text.split("\n")

    assert parts[0] == "--- x.py"
    assert parts[1] == "+++ x.py"
    assert parts[2].startswith("@@")
    assert " one" in parts
    assert "-two" in parts
    assert "+three" in parts


def test_new_file_diff_is_all_additions() -> None:
    parts = unified_diff("", "hello\n", path="new.py").split("\n")
    assert parts[0] == "--- new.py"
    assert parts[1] == "+++ new.py"
    assert "+hello" in parts


def test_diff_survives_text_without_a_trailing_newline() -> None:
    """结尾少了换行，不能让最后一行挤进上一条里。"""
    parts = unified_diff("a\nb", "a\nc", path="x.py").split("\n")

    assert parts[0] == "--- x.py"
    assert parts[1] == "+++ x.py"
    assert parts[2].startswith("@@")
    assert "-b" in parts
    assert "+c" in parts
    # 少了结尾换行的那一行被单独标出来，而不是和别的行挤在一起。
    assert parts[-1].endswith("\\ No newline at end of file")


def test_a_newline_only_change_still_shows_up() -> None:
    """只补了结尾换行也是一次改动，不能显示成「什么都没变」。"""
    parts = unified_diff("a\nb", "a\nb\n", path="x.py").split("\n")
    assert "-\\ No newline at end of file" in parts


def test_diff_lines_of_unchanged_text_is_empty() -> None:
    assert diff_lines(path="a.py", before="same\n", after="same\n") == []


# ----------------------------------------------------------- summarize_arguments


@pytest.mark.parametrize(
    ("name", "arguments", "lead"),
    [
        ("read_file", {"path": "dugentx/cli.py", "max_lines": 200}, "dugentx/cli.py"),
        ("write_file", {"path": "a/b.py", "content": "x"}, "a/b.py"),
        ("edit_file", {"path": "a/b.py", "old": "x", "new": "y"}, "a/b.py"),
        ("run_command", {"command": "uv run pytest -q", "timeout": 30}, "uv run pytest -q"),
    ],
)
def test_summary_leads_with_the_thing_a_coder_wants(
    name: str, arguments: dict[str, Any], lead: str
) -> None:
    summary = summarize_arguments(name, arguments)

    assert summary.startswith(lead)
    assert "\n" not in summary


def test_summary_keeps_the_other_arguments_visible() -> None:
    summary = summarize_arguments("read_file", {"path": "dugentx/cli.py", "max_lines": 50})

    assert summary.startswith("dugentx/cli.py")
    assert "max_lines=50" in summary


def test_a_long_body_is_cut_and_the_cut_is_stated() -> None:
    body = "x" * 500
    summary = summarize_arguments("write_file", {"path": "a.py", "content": body})

    assert summary.startswith("a.py")
    assert body not in summary
    # 被省掉的数量必须是真数出来的那个数，不能只说一句「已截断」。
    shown = summary.count("x")
    assert 0 < shown < len(body)
    assert str(len(body) - shown) in summary
    assert "未显示" in summary
    assert len(summary) < 200


def test_a_multiline_body_stays_on_one_line() -> None:
    summary = summarize_arguments("edit_file", {"path": "a.py", "old": "一\n二\n三"})

    assert "\n" not in summary
    assert "一 二 三" in summary


def test_summary_of_no_arguments_is_empty() -> None:
    assert summarize_arguments("run_command", {}) == ""


# --------------------------------------------------------------- 一行怎么长


def test_banner_says_which_model_which_session_in_which_directory() -> None:
    lines = banner_lines(model="gpt-x", session_id="sess-0123456789", cwd="C:/w", tools=4)

    assert lines[0] == "DugentX"
    assert "model=gpt-x" in lines[1]
    assert "23456789" in lines[1]  # 会话 id 只画尾巴
    assert "tools=4" in lines[1]
    assert "cwd=C:/w" in lines[2]


def test_banner_says_so_when_it_does_not_know_the_model() -> None:
    """「不知道」要写出来。空着的字段看起来像「没有这一项」。"""
    lines = banner_lines(model="", session_id="", cwd="", tools=0)

    assert "（未指定）" in lines[1]
    assert "（未开始）" in lines[1]
    assert "（未指定）" in lines[2]


def test_step_line_numbers_the_request() -> None:
    assert step_line(0) == "── step 0"


def test_tool_call_line_leads_with_the_name_then_the_summary() -> None:
    line = tool_call_line("read_file", {"path": "a.py"})
    assert line == "╭─ read_file  a.py"


def test_tool_call_line_without_arguments_has_no_trailing_space() -> None:
    assert tool_call_line("now", {}) == "╭─ now"


def test_a_long_tool_output_is_collapsed_and_counted() -> None:
    body = "\n".join(f"line {i}" for i in range(500))
    printed = tool_result_lines(name="run_command", content=body, ok=True, blocked=False)

    assert printed[0] == "│ ✓ run_command"
    assert len(printed) < 20  # 500 行不该原样落到屏幕上
    assert printed[1] == "│   line 0"
    assert printed[-1] == "│   line 499"

    shown = sum(1 for row in printed[1:] if not row.startswith("│   …"))
    omitted_line = next(row for row in printed if "省略" in row)
    omitted = int(re.search(r"省略 (\d+) 行", omitted_line).group(1))
    assert omitted == 500 - shown
    assert shown == RESULT_HEAD_LINES + RESULT_TAIL_LINES


def test_a_short_tool_output_is_printed_whole() -> None:
    printed = tool_result_lines(name="read_file", content="a\nb", ok=True, blocked=False)
    assert printed == ["│ ✓ read_file", "│   a", "│   b"]


def test_an_empty_tool_output_prints_only_the_header() -> None:
    printed = tool_result_lines(name="list_dir", content="", ok=True, blocked=False)
    assert printed == ["│ ✓ list_dir"]


@pytest.mark.parametrize(
    ("ok", "blocked", "expected"),
    [
        (True, False, "│ ✓ read_file"),
        (False, False, "│ ✗ read_file"),
        (False, True, "│ ⊘ read_file（被拦下）"),
    ],
)
def test_result_header_says_what_happened(ok: bool, blocked: bool, expected: str) -> None:
    printed = tool_result_lines(name="read_file", content="细节", ok=ok, blocked=blocked)
    assert printed[0] == expected


def test_a_long_single_line_is_clipped_with_a_count() -> None:
    printed = tool_result_lines(name="run_command", content="y" * 900, ok=True, blocked=False)

    assert "未显示" in printed[1]
    assert len(printed[1]) < 300


def test_question_renders_every_choice_key() -> None:
    question = Question(
        prompt="要执行这条命令吗？",
        detail="uv run pytest -q",
        choices=APPROVAL_CHOICES,
        title="run_command",
    )
    printed = "\n".join(question_lines(question))

    for choice in APPROVAL_CHOICES:
        assert f"[{choice.key}]" in printed
        assert choice.label in printed
    assert printed.startswith("? 要执行这条命令吗？")
    assert "uv run pytest -q" in printed
    assert "（默认）" in printed  # 回车是最常见的输入，默认项必须看得出来


def test_turn_end_is_exactly_one_line() -> None:
    line = turn_end_line(
        TurnResult(
            text="做完了",
            steps=2,
            tool_calls=3,
            usage=Usage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            stop_reason="completed",
        )
    )

    assert "\n" not in line
    assert "completed" in line
    assert "steps=2" in line
    assert "tools=3" in line
    assert "tokens=15" in line


def test_status_line_shows_every_field_it_knows() -> None:
    line = status_line(
        model="gpt-x", session_id="sess-0123456789", steps=7, tokens=42, permission="write→confirm"
    )

    assert "\n" not in line
    assert "gpt-x" in line
    assert "23456789" in line
    assert "sess-0123456789" not in line
    assert "steps=7" in line
    assert "tokens=42" in line
    assert "write→confirm" in line


def test_status_line_says_when_it_does_not_know_yet() -> None:
    line = status_line(model="", session_id="", steps=0, tokens=0)

    assert "（未指定）" in line
    assert "（未开始）" in line


def test_notice_prefixes_only_the_two_that_must_be_recognisable() -> None:
    assert notice_line("小心", kind="warn") == "! 小心"
    assert notice_line("只是旁白", kind="dim") == "只是旁白"
    assert error_line("炸了") == "✗ 炸了"


def test_the_three_markers_are_glyphs_not_words() -> None:
    """三种行首是「这句话是谁说的」在没有颜色时的唯一线索，所以它不能悄悄变。

    这里钉的是**屏幕上的那几个字符**：改字形是一次改版，改版要有意识，
    而不是某天顺手把 `•` 换成 `*`。
    """
    assert USER_MARKER == "• "
    assert ASSISTANT_MARKER == "▸ "
    assert ANSWER_MARKER == "◦ "


# ---------------------------------------------------------------------- 转圈


def test_the_spinner_cycles_through_every_frame_and_wraps() -> None:
    frames = [spinner_frame(index) for index in range(len(SPINNER_FRAMES))]

    assert len(set(frames)) == len(SPINNER_FRAMES), "帧要看得见地不一样"
    assert spinner_frame(len(SPINNER_FRAMES)) == frames[0]
    assert len(SPINNER_FRAMES) > 1


def test_a_still_spinner_is_a_single_glyph_not_a_slow_spinner() -> None:
    """关掉动效时给的是**静止**的那一个字形，不是「第一帧」。

    差别在这件事能不能被看出来：一个停在第一帧的转圈，和「我在干活」
    看起来一模一样；而 `⠿` 是一个填满的、不会往前走的点阵。
    """
    assert spinner_frame(0, animate=False) == SPINNER_STATIC
    assert spinner_frame(7, animate=False) == SPINNER_STATIC
    assert SPINNER_STATIC not in SPINNER_FRAMES


def test_elapsed_time_is_readable_at_a_glance() -> None:
    assert elapsed_text(0) == "0.0s"
    assert elapsed_text(4.24) == "4.2s"
    assert elapsed_text(12.36) == "12.4s"


def test_busy_text_is_the_frame_and_the_clock_only() -> None:
    """状态条最前面那一截里只有两样东西：转着的帧，和已经跑了多久。

    事实一个字都不在这里——它们由事件带来，和这个钟没有关系。
    """
    text = busy_text(frame=2, elapsed=4.24)

    assert text == f"{SPINNER_FRAMES[2]} 4.2s"
    assert busy_text(frame=2, elapsed=4.24, animate=False) == f"{SPINNER_STATIC} 4.2s"


# -------------------------------------------------------------------- 语气


@pytest.mark.parametrize(
    ("line", "kind"),
    [
        ("--- a.py", "diff_meta"),
        ("+++ a.py", "diff_meta"),
        ("@@ -1,2 +1,2 @@", "diff_meta"),
        ("+新的一行", "diff_add"),
        ("-旧的一行", "diff_del"),
        (USER_MARKER + "你好", "user"),
        (ASSISTANT_MARKER + "好", "assistant"),
        ("╭─ read_file  a.py", "tool"),
        ("│ ✓ read_file", "info"),
        ("│ ✗ read_file", "error"),
        ("│ ⊘ write_file（被拦下）", "warn"),
        ("│   细节行", "dim"),
        ("── step 1", "info"),
        ("── turn completed  steps=1", "dim"),
        ("? 执行吗？", "info"),
        ("! 小心", "warn"),
        ("✗ 炸了", "error"),
        ("  diff 上下文行", "dim"),
    ],
)
def test_classify_says_what_kind_of_line_this_is(line: str, kind: str) -> None:
    """每一行的语气只判定一次，两个界面都读它——所以这张表要钉住。"""
    assert classify(line) == kind


def test_every_kind_a_line_can_have_is_a_known_kind() -> None:
    """界面上色时用的那张表必须覆盖 `classify` 的所有返回值。

    漏一个的后果不是报错，是那一行悄悄没有颜色——而「悄悄」正是要防的。
    """
    from dugentx.tui.app import _STYLES

    produced = {
        classify(line)
        for line in (
            "--- a",
            "+a",
            "-a",
            USER_MARKER,
            ASSISTANT_MARKER,
            "╭─ x",
            "│ ✓ x",
            "│ ✗ x",
            "│ ⊘ x",
            "! x",
            "✗ x",
            "? x",
            "── step 1",
            "── turn completed",
            "什么都没有",
        )
    }
    assert produced <= set(_STYLES)


# ------------------------------------------------------------------ 命令表


def test_commands_are_recognised_and_split_from_their_argument() -> None:
    command, argument = commands.split("/model deepseek-chat")

    assert command is not None
    assert command.name == "/model"
    assert argument == "deepseek-chat"


def test_the_same_command_is_reachable_through_its_alias() -> None:
    assert commands.split("/exit")[0] is commands.split("/quit")[0]


def test_a_plain_sentence_is_not_a_command() -> None:
    assert commands.split("帮我看一下这个文件") == (None, "")
    assert commands.is_command("帮我看一下这个文件") is False


def test_an_unknown_command_is_not_a_prompt_and_gets_an_explanation() -> None:
    """不认识的命令不能悄悄变成用户的话——那是一次没说出口的请求改写。"""
    assert commands.is_command("/nope") is True
    assert commands.split("/nope") == (None, "")

    lines = commands.unknown_lines("/nope")
    assert "/nope" in lines[0]
    assert "/help" in lines[1]
    assert "/model" in lines[1]


def test_help_lists_every_command_and_its_keys() -> None:
    lines = "\n".join(commands.help_lines([("Ctrl-Q", "退出")]))

    for command in commands.COMMANDS:
        assert command.name in lines
        assert command.summary in lines
    assert "Ctrl-Q" in lines
    # /clear 只清屏幕，不动会话日志——这一条必须写在帮助里，否则没人知道。
    assert "会话日志不动" in lines


def test_help_says_commands_are_not_sent_to_the_model() -> None:
    assert "不会发给模型" in commands.help_lines()[0]


# ------------------------------------------------------------------ 补全


def labels(completion: Any) -> list[str]:
    return [item.label for item in completion.items]


def values(completion: Any) -> list[str]:
    """候选**填进去**的那一段。用法（`/model [名字]`）只出现在屏幕上。"""
    return [item.value for item in completion.items]


def vocabulary() -> Any:
    return commands.Vocabulary(
        current_model="deepseek-flash",
        providers=("deepseek", "mistral", "openai"),
        adapters=("replay",),
    )


def test_a_bare_slash_offers_every_command_in_the_table_order() -> None:
    completion = commands.completion_for("/")

    assert completion is not None
    assert completion.mode == "command"
    assert values(completion) == commands.known_names()
    assert values(completion)[0] == "/help", "第一条就是默认高亮的那条，顺序不能随机"
    assert "/model [名字]" in labels(completion), "行首是用法：要不要参数要看得见"


def test_typing_narrows_the_commands_and_aliases_count_as_commands() -> None:
    """别名单独成一条：打 `ex` 的人要看到的那一行是 `/exit`，否则他会以为没补到。"""
    narrow = commands.completion_for("/st")
    alias = commands.completion_for("/ex")

    assert narrow is not None and values(narrow) == ["/status"]
    assert alias is not None and values(alias) == ["/exit"]
    assert "/quit" in alias.items[0].detail


def test_a_subsequence_is_enough_to_find_a_command() -> None:
    """`/prv` 也该找得到 `/provider`——这正是模糊匹配而不是 `in` 的意义。"""
    completion = commands.completion_for("/prv")

    assert completion is not None
    assert values(completion) == ["/provider"]


def test_a_fully_typed_command_has_nothing_left_to_complete() -> None:
    """打完的就不提示了。这条不是为了省屏幕，是为了**回车还能用**。

    弹出开着的时候回车是「接受候选」，所以一个和输入框一字不差的候选会把回车
    一直吃下去——接受一个没有变化的值等于什么都没做，那条命令就再也跑不起来。
    """
    assert commands.completion_for("/help") is None
    assert commands.completion_for("/exit") is None, "别名也一样"
    assert commands.completion_for("/model deepseek-flash", vocabulary()) is None
    assert commands.completion_for("/provider mistral", vocabulary()) is None
    assert commands.completion_for("/hel") is not None, "差一个字就还有得补"


def test_nothing_looks_like_a_command_so_there_is_nothing_to_complete() -> None:
    """一行普通的话不该让弹出冒出来。"""
    assert commands.completion_for("帮我看一下这个文件") is None
    assert commands.completion_for("") is None
    assert commands.completion_for("/nope") is None
    assert commands.completion_for("/no pe") is None, "打错的命令名不该继续给参数的候选"


def test_only_commands_that_take_an_argument_offer_argument_candidates() -> None:
    assert commands.completion_for("/help ") is None
    assert commands.completion_for("/clear ") is None
    assert commands.completion_for("/model ", vocabulary()) is not None


def test_without_a_vocabulary_an_argument_has_no_candidates_at_all() -> None:
    """没有 `ctx.models`（这次组合里没有 llm 插件）时，参数补不出来就是补不出来。

    给不出候选就**不弹**：一个空列表比「有这个功能，但你就看个空框」诚实。
    """
    assert commands.completion_for("/model ", commands.Vocabulary()) is None
    assert commands.completion_for("/provider ", commands.Vocabulary()) is None
    assert commands.completion_for("/mo") is not None, "命令名不欠上下文，照样补"


def test_the_argument_of_model_is_the_model_in_play() -> None:
    """`/model` 补的是**模型名**，而离线唯一知道的那个就是现在在用的那个。

    往它后面填一个 provider 名会得到一个命令会当成模型名去用的值——补出一个
    错值比补不出来糟，所以宁少不假。
    """
    completion = commands.completion_for("/model deep", vocabulary())

    assert completion is not None
    assert completion.mode == "argument"
    assert labels(completion) == ["deepseek-flash"]
    assert completion.items[0].detail == "当前模型"
    assert completion.apply("/model deep", completion.items[0]) == "/model deepseek-flash"


def test_a_model_prefix_nobody_knows_offers_nothing() -> None:
    assert commands.completion_for("/model qwen", vocabulary()) is None


def test_the_argument_of_provider_is_exactly_what_the_picker_lists() -> None:
    """补全和选择器必须是同一份名单：两份名单会长成两个「可选的东西」的答案。"""
    completion = commands.completion_for("/provider ", vocabulary())

    assert completion is not None
    assert labels(completion) == ["deepseek", "mistral", "openai", "replay"]
    assert completion.items[-1].detail.startswith("已注册的适配器")
    assert completion.apply("/provider ", completion.items[1]) == "/provider mistral"


def test_accepting_a_command_name_leaves_a_space_for_the_argument() -> None:
    completion = commands.completion_for("/mo")

    assert completion is not None
    assert completion.apply("/mo", completion.items[0]) == "/model "


def test_accepting_a_command_name_keeps_what_was_already_typed() -> None:
    """补的是**光标前那一段**，行的其余部分一个字不动。"""
    completion = commands.completion_for("/model deep", vocabulary())

    assert completion is not None
    assert completion.apply("/model deep", completion.items[0]) == "/model deepseek-flash"


def test_leading_whitespace_does_not_change_what_can_be_completed() -> None:
    """手滑多打一个空格，这一行仍然是一条命令（`is_command` 就是这么算的）。"""
    completion = commands.completion_for("  /mo")

    assert completion is not None
    assert completion.apply("  /mo", completion.items[0]) == "  /model "


# ------------------------------------------------------------------ 匹配


def test_ranking_keeps_the_given_order_when_scores_are_equal() -> None:
    """同分保序，因为**第一条就是 Tab 会替你按下去的那条**。

    命令表的顺序是「先看、再改、最后走」，参数补全是「当前那个先给」——
    字母序会把这个判断抹掉。
    """
    from dugentx.tui.fuzzy import rank

    items = ["zzz-a", "aaa-b", "mmm-c"]
    assert rank(items, "", key=str) == items
    assert rank(items, "", key=str)[0] == "zzz-a"


def test_ranking_prefers_the_better_match_over_the_earlier_one() -> None:
    from dugentx.tui.fuzzy import rank

    items = ["status", "model", "mistral"]
    assert rank(items, "mi", key=str) == ["mistral"]
    assert rank(items, "mo", key=str) == ["model"]
