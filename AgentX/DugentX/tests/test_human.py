"""human 缝的测试：通道本身、走通道的审批、以及「问人」那个工具。

这组测试要说清的是一件事：**审批不再认识终端**。
它只认识「问一句、拿一个答案」，于是终端、TUI、CI 里的自动回答
都是同一个位置插进来的东西——这正是「加一个 TUI 不用改审批」的证明。
"""

from __future__ import annotations

import io
from typing import Any

from dugentx.kernel.context import Context
from dugentx.kernel.events import EventBus
from dugentx.providers.approval_channel import ChannelApproval
from dugentx.providers.human_stdio import StdioHuman
from dugentx.seams.human import ALWAYS, NO, YES, Answer, Choice, Question
from dugentx.seams.permissions import ApprovalRequest


class FakeTTY(io.StringIO):
    """一个「是终端」的流。测试不该需要一个真的终端。"""

    def __init__(self, *, tty: bool = True) -> None:
        super().__init__()
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def scripted(*answers: str) -> Any:
    """按顺序回答的假输入函数。"""
    queue = list(answers)

    def ask(_prompt: str) -> str:
        if not queue:
            raise EOFError
        return queue.pop(0)

    return ask


def q(prompt: str = "选一个", *, choices: tuple[Choice, ...] = (), title: str = "") -> Question:
    return Question(prompt=prompt, choices=choices, title=title)


# ------------------------------------------------------------------ 通道


async def test_a_non_tty_channel_refuses_to_pretend_it_can_ask() -> None:
    """不是终端就明说没人能回答。

    一个在 CI 里挂住等输入的 harness，比一个当场说「不」的糟糕得多——
    前者会烧掉一整条流水线的时间，后者只会让这次调用失败。
    """
    channel = StdioHuman(stream=FakeTTY(tty=False), out=io.StringIO(), ask=scripted("y"))
    assert channel.interactive() is False

    answer = await channel.ask(q())
    assert answer.cancelled
    assert not answer


async def test_ask_returns_what_the_human_typed() -> None:
    out = io.StringIO()
    channel = StdioHuman(stream=FakeTTY(), out=out, ask=scripted("把日志改成 JSON"))
    answer = await channel.ask(q("输出格式用哪种？", choices=()))

    assert answer.text == "把日志改成 JSON"
    assert not answer.cancelled
    assert "输出格式用哪种？" in out.getvalue()


async def test_choose_takes_a_single_key() -> None:
    channel = StdioHuman(stream=FakeTTY(), out=io.StringIO(), ask=scripted("y"))
    question = q("要写文件吗", choices=(Choice.yes(), Choice.no()), title="write_file")
    assert (await channel.choose(question)).key == YES


async def test_choose_accepts_enter_as_the_default() -> None:
    """回车是最常见的输入，它必须等于默认项，而不是等于「无效」。"""
    channel = StdioHuman(stream=FakeTTY(), out=io.StringIO(), ask=scripted(""))
    question = q("要写文件吗", choices=(Choice.yes(), Choice.no()), title="write_file")
    assert (await channel.choose(question)).key == NO


async def test_choose_re_asks_on_an_unknown_key() -> None:
    out = io.StringIO()
    channel = StdioHuman(stream=FakeTTY(), out=out, ask=scripted("z", "n"))
    question = q("要写文件吗", choices=(Choice.yes(), Choice.no()), title="write_file")

    assert (await channel.choose(question)).key == NO
    assert "只能选" in out.getvalue()


async def test_an_empty_keypress_while_typing_does_not_cancel() -> None:
    """`Answer.cancelled` 和「回答了空字符串」是两件事，不能合并。"""
    channel = StdioHuman(stream=FakeTTY(), out=io.StringIO(), ask=lambda _p: "")
    answer = await channel.ask(q("再说一遍？"))
    assert answer.text == ""
    assert answer.cancelled is False


async def test_eof_is_a_cancelled_answer_not_a_crash() -> None:
    channel = StdioHuman(stream=FakeTTY(), out=io.StringIO(), ask=scripted())
    answer = await channel.ask(q())
    assert answer.cancelled


def test_note_goes_to_the_injected_stream() -> None:
    out = io.StringIO()
    channel = StdioHuman(stream=FakeTTY(), out=out, ask=scripted())
    channel.note("这件事失败了", kind="error")
    assert "这件事失败了" in out.getvalue()


# ------------------------------------------------------------------ 审批


class FakeChannel:
    """一个假装在问人的通道。它记录被问到的问题，并按脚本作答。"""

    name = "fake"

    def __init__(self, *answers: str, interactive: bool = True) -> None:
        self._answers = list(answers)
        self._interactive = interactive
        self.questions: list[Question] = []

    def interactive(self) -> bool:
        return self._interactive

    async def ask(self, question: Question) -> Answer:
        self.questions.append(question)
        return Answer(text=self._answers.pop(0) if self._answers else "", source=self.name)

    async def choose(self, question: Question) -> Answer:
        return await self.ask(question)

    def note(self, text: str, *, kind: str = "info") -> None:
        pass


def request(tool: str = "write_file") -> ApprovalRequest:
    return ApprovalRequest(
        tool=tool, labels=frozenset({"write"}), arguments={"path": "a.txt"}, level="confirm"
    )


async def test_no_channel_means_refuse_with_a_reason_you_can_act_on() -> None:
    approval = ChannelApproval(lambda: None)
    decision = await approval.decide(request())

    assert decision.allowed is False
    assert "没有可问的人" in decision.reason


async def test_a_non_interactive_channel_is_refused_not_awaited() -> None:
    approval = ChannelApproval(lambda: FakeChannel(interactive=False))
    decision = await approval.decide(request())

    assert decision.allowed is False
    assert "不是交互式环境" in decision.reason


async def test_yes_allows_and_no_refuses() -> None:
    channel = FakeChannel(YES)
    assert (await ChannelApproval(lambda: channel).decide(request())).allowed is True

    channel = FakeChannel(NO)
    assert (await ChannelApproval(lambda: channel).decide(request())).allowed is False


async def test_always_allows_and_stops_asking() -> None:
    """`a` 必须存在：每次都要再按一次 y 的审批，只会训练出不停按 y 的人。"""
    channel = FakeChannel(ALWAYS, YES)
    approval = ChannelApproval(lambda: channel)

    assert (await approval.decide(request())).allowed is True
    assert "write_file" in approval.session_allow

    await approval.decide(request())
    assert len(channel.questions) == 1, "记住之后不该再问"


async def test_cancelling_the_question_refuses_rather_than_hanging() -> None:
    channel = FakeChannel()  # 没有脚本 = 返回空答案
    decision = await ChannelApproval(lambda: channel).decide(request())
    assert decision.allowed is False


async def test_the_question_carries_the_request_for_a_human_to_judge() -> None:
    channel = FakeChannel(YES)
    await ChannelApproval(lambda: channel).decide(request())

    question = channel.questions[0]
    assert "write_file" in question.detail
    assert "a.txt" in question.detail
    assert {c.key for c in question.choices} == {YES, ALWAYS, NO}


async def test_the_approval_emits_events_so_the_ui_can_show_it() -> None:
    bus = EventBus()
    asked: list[Any] = []
    bus.on("human/asked", lambda *a: asked.append(a))
    bus.on("human/answered", lambda *a: asked.append(a))

    approval = ChannelApproval(lambda: FakeChannel(YES), events=bus)
    await approval.decide(request())

    assert len(asked) == 2


async def test_an_unrecognised_answer_is_refused_not_treated_as_yes() -> None:
    """认不出来就是拒绝。这里猜错的方向是放行，代价太大。"""
    approval = ChannelApproval(lambda: FakeChannel("maybe"))
    decision = await approval.decide(request())
    assert decision.allowed is False


# ------------------------------------------------------------------ 问人工具


async def test_the_ask_tool_says_so_when_there_is_no_channel() -> None:
    from dugentx.tools.ask_tools import ask_human

    ctx = Context("t")
    reply = await ask_human(ctx, "你喜欢哪种？")
    assert "问不到人" in reply


async def test_the_ask_tool_passes_the_answer_back_and_logs_the_exchange() -> None:
    from dugentx.tools.ask_tools import ask_human

    ctx = Context("t")
    seen: list[str] = []
    ctx.events.on("human/asked", lambda *_a: seen.append("asked"))
    ctx.events.on("human/answered", lambda *_a: seen.append("answered"))
    ctx.provide("human", FakeChannel("先写测试"))

    reply = await ask_human(ctx, "先写测试还是先写实现？")

    assert reply == "先写测试"
    assert seen == ["asked", "answered"]


async def test_the_ask_tool_does_not_pretend_an_absent_answer_is_an_answer() -> None:
    from dugentx.tools.ask_tools import ask_human

    ctx = Context("t")
    ctx.provide("human", FakeChannel())  # 通道在，但没人答
    reply = await ask_human(ctx, "在吗")
    assert "没有给出内容" in reply


def test_approval_offers_exactly_the_three_keys_with_distinct_meanings() -> None:
    """`ALWAYS` 必须存在：每次都要再按一次 y 的审批，等于没有审批。"""
    from dugentx.seams.human import APPROVAL_CHOICES

    keys = [c.key for c in APPROVAL_CHOICES]
    assert keys == [YES, ALWAYS, NO]
    assert len(set(keys)) == len(keys)


def test_the_default_choice_is_the_cautious_one() -> None:
    """默认必须是拒绝。手快敲回车不该等于同意。"""
    from dugentx.seams.human import APPROVAL_CHOICES

    question = Question(prompt="?", choices=APPROVAL_CHOICES)
    assert question.default_key() == NO
