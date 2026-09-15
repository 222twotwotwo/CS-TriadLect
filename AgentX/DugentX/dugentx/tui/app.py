"""app —— textual 上的那个界面。

它和 harness 的关系只有一句话：**它是一个观察者，加一个输入源。**
回合还是循环在跑（`agent.send`），工具还是管道在执行，审批还是走 human 缝；
这个文件订阅那些本来就有的事件，把它们画出来，再把输入框里的东西交出去。
把它整个删掉，harness 一行都不用改——循环照跑、日志照记、工具照执行，
只是没人看而已。

三件事在这里碰头，边界写清楚：

- `TuiApp`：画面和输入。它不碰 harness 的内部，只调 `agent.send`。
- `TextualHuman`：`ctx.human` 的实现。审批问的就是它——所以装了这个界面之后，
  「问人」这件事仍然只有一条通道。
- 弹窗和提示（补全、provider 选择器、审批）在 `widgets.py`。

**两种模式，一份真相。** `--once`（脚本、CI、管道）走 `plain.py`，一行行文字；
真终端走这里。两边的排版是 `formatting.py` 里同一批函数，事件翻译是同一个画师
（`paint.py`）——差别只在渲染调用最后落到哪个目的地（`RenderSwitch`）。
所以「屏幕上看到的」和「管道里拿到的」不会变成两件不同的事。

关于「谁在读数」：主循环在 `await agent.send(...)` 的时候是挂起的，此时审批才可能
来问人。textual 也是这么排的——回合进行中输入框照样能打字，但真正等答案的地方
只有一处（`_answer_waiter`）。**这一点是设计前提，不是巧合。**
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable, Iterable
from functools import partial
from typing import IO, TYPE_CHECKING, Any, ClassVar

from rich.markup import escape
from textual.app import App, ComposeResult, SystemCommand
from textual.binding import Binding
from textual.widgets import Input, RichLog, Static

from dugentx.kernel.context import Context
from dugentx.kernel.errors import DuGentXError
from dugentx.seams.human import ALWAYS, Answer, Choice, NoteKind, Question
from dugentx.tui import commands
from dugentx.tui.commands import COMMANDS, SlashCommand
from dugentx.tui.formatting import (
    ANSWER_MARKER,
    ASSISTANT_MARKER,
    USER_MARKER,
    StatusUpdate,
    banner_lines,
    block_lines,
    classify,
    diff_lines,
    error_line,
    notice_line,
    question_lines,
    status_line,
    step_line,
    tool_call_line,
    tool_result_lines,
    turn_end_line,
)
from dugentx.tui.paint import RenderSwitch
from dugentx.tui.plain import PlainTui, TuiConfig, runtime_facts
from dugentx.tui.widgets import ChoiceModal, ProviderPicker, StatusBar, SuggestionPopup

if TYPE_CHECKING:
    from dugentx.providers.model_switch import ModelChoice

DEFAULT_PLACEHOLDER = "说点什么，或者 /help"

_REVEAL_SECONDS = 0.22
"""助手那一行开头的亮相有多长。

只做这一处动效，而且刻意短：动效的作用是「这里刚发生了一件事」，
超过这个长度，它自己就成了要等的东西。"""

_REVEAL_OPACITY = 0.45
"""亮相从多暗升起来。半暗而不是全暗：全暗的那一下看着像内容还没到。"""

_STYLES: dict[str, str] = {
    "user": "bold cyan",
    "assistant": "white",
    "tool": "magenta",
    "info": "cyan",
    "success": "green",
    "warn": "yellow",
    "error": "bold red",
    "dim": "dim",
    "diff_add": "green",
    "diff_del": "red",
    "diff_meta": "bold cyan",
}
"""语气 → 颜色。颜色只在这里出现一次，而「这一行是什么语气」由
`formatting.classify` 一处判定——两边各写一份的话，加一种行就会漏掉一边。"""

_APP_CSS = """
Screen {
    layout: vertical;
}
#transcript {
    height: 1fr;
    padding: 0 1;
}
#stream {
    height: auto;
    padding: 0 1;
}
#prompt {
    border: none;
    height: 1;
    padding: 0 1;
    background: $surface;
}
"""


def _key_label(key: str) -> str:
    """`ctrl+o` → `Ctrl+O`。键位表是写给人看的，不是给解析器看的。"""
    return "+".join(part.capitalize() for part in key.split("+"))


def _model_label(choice: ModelChoice | None) -> str:
    return choice.describe() if choice is not None else "（还没配）"


# ------------------------------------------------------------------ 流水账


class TranscriptRenderer:
    """把 `formatting` 产出的行写进画面上的流水账。

    只做两件事：按语气上色，以及记住「助手那一行还没写完」。后者必须留在这一层，
    因为调用方每次只给一小块增量——「上一块和这一块属于同一行」只有渲染器自己知道。

    写完的行进 `RichLog`，正在写的那一行挂在一个单独的 `Static` 上。分开是因为
    RichLog 的每一行都是写完的：一个还在长的句子需要的是「替换最后一行」，
    而那不是它擅长的事。
    """

    def __init__(self, log: RichLog, stream: Static) -> None:
        self._log = log
        self._stream = stream
        self._pending = ""

    # ---------------------------------------------------------------- 内部

    def _markup(self, line: str) -> str:
        """一行 → rich 标记。

        `escape` 不能省：工具名、路径、diff 内容里出现方括号是常事，不转义的话
        `[tool]` 会被当成标记吃掉——屏幕上少了几个字，而且只在特定内容下才发生。
        """
        style = _STYLES.get(classify(line), "")
        body = escape(line)
        return f"[{style}]{body}[/]" if style else body

    def _write(self, line: str) -> None:
        self._log.write(self._markup(line))

    def _lines(self, lines: Iterable[str]) -> None:
        self.assistant_end()
        for line in lines:
            self._write(line)

    # ---------------------------------------------------------------- 会话

    def banner(self, *, model: str, session_id: str, cwd: str, tools: int) -> None:
        self._lines(banner_lines(model=model, session_id=session_id, cwd=cwd, tools=tools))

    def user(self, text: str) -> None:
        self._lines(block_lines(USER_MARKER, text))

    def assistant_delta(self, text: str) -> None:
        if not text:
            return
        if not self._pending:
            self._pending = ASSISTANT_MARKER
            # 新的一行开头了：让它从半亮升到全亮。整个界面只有这一处亮相，
            # 因为它是唯一一个「正在长出来」的条目——已经写完的行进的是
            # RichLog，为它们逐行做动画要另写一套渲染，换来的只是好看一点。
            self._reveal()
        self._pending += text
        self._stream.update(self._markup(self._pending))

    def _reveal(self) -> None:
        """一次很短的亮相：暗一点，然后亮回来。

        动的是 `styles.opacity`（那条规则本身可以设），而**不是**调
        `widget.animate("opacity", ...)`：textual 8 里 `Widget.opacity` 是一个
        算出来的只读属性（祖宗的 opacity 相乘），动画跑到第一帧就会在
        `setattr` 上抛出来——而那是异步的，界面会在动效中途炸掉。
        这里宁可多写一行注释，也不留一个「大部分时候没事」的写法。

        动效是装饰，所以它**不许**带走一次渲染：界面还没挂上、动画被关掉、
        平台不支持——任何一种情况都只是不亮而已，正文照样写下去。
        """
        styles = self._stream.styles
        try:
            styles.opacity = _REVEAL_OPACITY
            self._stream.app.animator.animate(
                styles, "opacity", 1.0, duration=_REVEAL_SECONDS, easing="out_cubic"
            )
        except Exception:
            styles.opacity = 1.0

    def assistant_end(self) -> None:
        """收尾：把这一行交给流水账。没开着就什么都不做，所以调两次是安全的。"""
        if not self._pending:
            return
        pending, self._pending = self._pending, ""
        self._stream.update("")
        self._write(pending)

    def step(self, index: int) -> None:
        self._lines([step_line(index)])

    def tool_call(self, name: str, arguments: dict[str, Any]) -> None:
        self._lines([tool_call_line(name, arguments)])

    def tool_result(self, *, name: str, content: str, ok: bool, blocked: bool) -> None:
        self._lines(tool_result_lines(name=name, content=content, ok=ok, blocked=blocked))

    def diff(self, *, path: str, before: str, after: str) -> None:
        self._lines(diff_lines(path=path, before=before, after=after))

    def notice(self, text: str, *, kind: str = "info") -> None:
        self._lines([notice_line(text, kind=kind)])

    def error(self, text: str) -> None:
        self._lines([error_line(text)])

    def question(self, question: Question) -> None:
        self._lines(question_lines(question))

    def answer(self, text: str) -> None:
        self._lines(block_lines(ANSWER_MARKER, text))

    def turn_end(self, result: Any) -> None:
        self._lines([turn_end_line(result)])

    # ---------------------------------------------------------------- 清屏

    def clear(self) -> None:
        """清掉屏幕上的流水账。**不动**任何别的东西——见 `TuiApp.action_clear`。"""
        self._pending = ""
        self._stream.update("")
        self._log.clear()


# ------------------------------------------------------------------ human 通道


class TextualHuman:
    """`ctx.human` 在装了界面时的实现。

    行为刻意与 stdio 通道**完全一致**，只是长得不一样：同样是 `ask` / `choose` /
    `note`，同样在没人能回答时拒绝而不是干等。差别只在呈现——这让「换通道」不影响
    任何一行调用方代码，也让审批逻辑可以在没有界面的环境里被测试。

    三条规矩，每条都是为了让审批不被用成复选框：

    1. **不是终端、或者界面没在跑，就拒绝。** `interactive()` 为假，审批据此
       直接说「不」；CI 里跑同一份配置不会挂住等一个永远不会来的按键。
    2. **`a`（本会话内都允许）记住一整个会话**，粒度是提问的**标题**（审批传的
       是工具名）。每次都要再按一次 y，只会训练出不停按 y 的人，那时候这道门
       就只剩装饰作用了。
    3. **默认项是「拒绝」。** 回车是最常见的输入，所以它必须通向最保守的那个答案。
    """

    name = "tui"

    def __init__(self, app: TuiApp, *, tty: bool) -> None:
        self._app = app
        self._tty = tty
        self._remembered: set[str] = set()

    def interactive(self) -> bool:
        return self._tty and self._app.is_running

    def note(self, text: str, *, kind: NoteKind = "info") -> None:
        """单向告知。界面没在跑就落到纯文本那条路——两条路共用一份排版。"""
        if text:
            self._app.renderer.notice(text, kind=kind)

    async def ask(self, question: Question) -> Answer:
        if not self.interactive():
            return Answer(cancelled=True, source=self.name)
        text = await self._app.ask_text(question)
        if text is None:
            return Answer(cancelled=True, source=self.name)
        return Answer(text=text, source=self.name)

    async def choose(self, question: Question) -> Answer:
        if not self.interactive():
            return Answer(cancelled=True, source=self.name)
        if self._already_allowed(question):
            # 这个标题（审批传的是工具名）已经答过一次「本会话内都允许」了，
            # 所以不再问第二遍——回答仍然是当时那个回答。再问一遍，
            # 只会让这个机制显得随意，也会训练出不停按同一个键的人。
            return Answer(text=ALWAYS, source=self.name)

        key = await self._app.choose_key(question, tuple(question.choices))
        if key is None:
            return Answer(cancelled=True, source=self.name)
        if key == ALWAYS and question.title:
            self._remembered.add(question.title)
        return Answer(text=key, source=self.name)

    @property
    def remembered(self) -> frozenset[str]:
        """本会话里被「一律允许」过的提问标题（审批传的是工具名）。"""
        return frozenset(self._remembered)

    def _already_allowed(self, question: Question) -> bool:
        """这个问题是不是已经答过「本会话内都允许」了。

        粒度是**标题**，不是文件、不是参数：审批传的标题是工具名，所以对
        `edit_file` 选过一次「一律允许」之后，这一个会话里所有 `edit_file`
        都不再问。想更细（比如只放行某个目录）属于 `permissions` 缝的规则，
        不该塞进界面——界面记不住那么多上下文，也不该记。
        """
        if not question.title or question.title not in self._remembered:
            return False
        return any(choice.key == ALWAYS for choice in question.choices)


# ------------------------------------------------------------------ 应用


class TuiApp(App[None]):
    """编码 TUI：一个滚动流水账、一个输入框、一条状态条、一个补全提示、两个弹窗。"""

    TITLE = "DugentX"
    SUB_TITLE = "编码 TUI"
    ENABLE_COMMAND_PALETTE = True
    CSS = _APP_CSS
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("ctrl+o", "pick_provider", "换 provider / 模型"),
        Binding("ctrl+l", "clear", "清屏"),
        Binding("up", "history_previous", "上一条输入 / 上一条候选", show=False),
        Binding("down", "history_next", "下一条输入 / 下一条候选", show=False),
        Binding("tab", "complete", "补全候选", priority=True),
        Binding("enter", "accept_completion", "接受候选", priority=True),
        Binding("escape", "cancel_prompt", "取消提问 / 收起补全", show=False),
        Binding("ctrl+d", "leave", "退出（输入框空的时候）", show=False, priority=True),
    ]
    """键位。

    `Ctrl-D` 是 `priority` 的：`Input` 自己也想要这个键（删光标右边一个字符）。
    两个语义抢一个键，只能选一个——而按 Ctrl-D 的人多半是想退出，
    所以「退出」赢。想删右边一个字符，还有 `Delete` 键。

    `Tab` 和 `Enter` 也是 `priority` 的，理由不同：`Enter` 在 `Input` 上是
    「提交」，`Tab` 在屏幕上通常是「挪焦点」，两个都比「接受候选」更早拿到键。
    抢在前面之后，**平时必须把它们让回去**——靠的是 `check_action`：
    候选没开着的时候这两条绑定等于不存在，提交和挪焦点一切照旧。
    一个永远生效的绑定会把回车吞掉，而那正是最不能出错的那个键。
    """

    def __init__(
        self,
        ctx: Context,
        *,
        config: TuiConfig | None = None,
        stream: IO[str] | None = None,
        tty: bool = False,
    ) -> None:
        super().__init__()
        self._ctx = ctx
        self._config = config or TuiConfig()
        self._plain = PlainTui(ctx, config=self._config, stream=stream)
        self._renderer = RenderSwitch(self._plain.renderer)
        self._tty = tty
        self._human = TextualHuman(self, tty=tty)
        self._transcript: TranscriptRenderer | None = None
        self._bar: StatusBar | None = None
        self._popup: SuggestionPopup | None = None
        self._programmatic: str | None = None
        """上一次由程序（历史、补全）塞进输入框的那一行。见 `on_input_changed`。"""
        self._history: list[str] = []
        self._recall: int | None = None
        self._answer_waiter: asyncio.Future[str | None] | None = None

    # ---------------------------------------------------------------- 接口

    @property
    def ctx(self) -> Context:
        return self._ctx

    @property
    def renderer(self) -> RenderSwitch:
        """渲染目的地。画师（`paint.py`）拿到的就是这个开关。"""
        return self._renderer

    @property
    def plain(self) -> PlainTui:
        """非交互那条路。`--once` 用它，界面退场之后也回到它。"""
        return self._plain

    @property
    def human(self) -> TextualHuman:
        return self._human

    @property
    def status_sink(self) -> Callable[[StatusUpdate], None]:
        """状态条的收件人。插件把它接在画师上（见 `dugentx/plugins/tui.py`）。"""
        return self.update_status

    # ---------------------------------------------------------------- 装配

    def compose(self) -> ComposeResult:
        yield RichLog(id="transcript", markup=True, wrap=True, highlight=False)
        yield Static("", id="stream", markup=True)
        yield SuggestionPopup(id="suggestions")
        yield Input(placeholder=self._placeholder(), id="prompt")
        yield StatusBar(id="status", animate=self._config.animate)

    def on_mount(self) -> None:
        self._transcript = TranscriptRenderer(
            self.query_one("#transcript", RichLog), self.query_one("#stream", Static)
        )
        self._bar = self.query_one("#status", StatusBar)
        self._popup = self.query_one("#suggestions", SuggestionPopup)
        # 界面起来了：画师从现在开始画到这里，而不是写到标准输出。
        self._renderer.use(self._transcript)
        self._seed_status()
        if self._config.show_banner:
            self._renderer.banner(**runtime_facts(self._ctx))
        self.query_one("#prompt", Input).focus()

    def on_unmount(self) -> None:
        # 退场之后可能还有事件在路上（比如一个回合还没跑完）。让它们写回纯文本，
        # 而不是写进一组已经不在屏幕上的控件里。
        self._transcript = None
        self._bar = None
        self._popup = None
        self._renderer.use(self._plain.renderer)

    # ---------------------------------------------------------------- 状态条

    def update_status(self, update: StatusUpdate) -> None:
        """画师推过来的一次状态更新（插件把它接在这里）。

        只在界面真的在的时候写：状态条是画出来的东西，不是状态本身——
        没有界面的时候，这些事实仍然在会话日志里。
        """
        if self._bar is not None:
            self._bar.update_status(update)

    def _seed_status(self) -> None:
        """界面刚起来时的初值：会话、模型、权限档位。

        这几项不会有「变化事件」——它们一开始就是这样。状态条的第一帧必须
        自己把它们读出来，否则它会一直空着，直到某件事恰好发生。
        """
        facts = runtime_facts(self._ctx)
        self.update_status(
            StatusUpdate(
                model=str(facts["model"]),
                session_id=str(facts["session_id"]),
                permission=self._permission_text(),
            )
        )

    def _permission_text(self) -> str:
        """权限档位。读不到就不写这一项——空着比编一个「默认」诚实。"""
        policy = self._ctx.get("permissions")
        describe = getattr(policy, "describe", None)
        if not callable(describe):
            return ""
        try:
            return str(describe())
        except Exception:
            return ""

    def _status_facts(self) -> dict[str, Any]:
        """状态条的此刻状态。`/status` 读它就够了——一处真相，两处呈现。"""
        if self._bar is not None:
            return self._bar.state
        facts = runtime_facts(self._ctx)
        return {
            "model": str(facts["model"]),
            "session_id": str(facts["session_id"]),
            "steps": 0,
            "tokens": 0,
            "permission": self._permission_text(),
        }

    # ---------------------------------------------------------------- 输入

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """`Tab` / `Enter` 只在**有候选**的时候归界面管。

        返回 `False` 是让这条绑定**不生效**，而不是「什么都不做」——差别在于那个键
        会不会被吞掉。一条永远生效的绑定会把回车抢走，于是正常的提交再也不会发生，
        而回车是这台机器上最常用的键：这条不能靠运气。

        `Tab` 同理：没得补的时候它必须落回别处（`Screen` 的挪焦点），
        而不是安静地消失。所以判据是「这一行有没有候选」，不是「弹出开没开」——
        翻历史翻出来的一条命令也有候选，只是弹出还没被叫出来。
        """
        if action == "complete":
            return self._candidates() is not None
        if action == "accept_completion":
            return self._popup is not None and self._popup.completion is not None
        return True

    def on_input_changed(self, event: Input.Changed) -> None:
        """输入框里每变一个字，就重算一次候选。

        边打边算，而不是等一个 Tab 再算：弹出要**在打到 `/` 的那一刻**就出现，
        那才是「这里有一条命令可以补」这个提示起作用的时候。

        程序塞进去的那一行（历史、补全）不算「打字」，见 `_set_prompt`。
        判断用的是**那一行本身**，不是一个「下一次变化就作废」的开关：
        赋一个和现在一样的值不会产生 `Changed`，开关就会留到下一次真的打字时
        才被读到——那时候关掉的是人打出来的候选。
        """
        if event.input.id != "prompt":
            return
        if self._answering:
            # 这一行是给提问的回答，不是命令。见 `_answering`。
            self._dismiss_suggestions()
            return
        if self._programmatic is not None and event.value == self._programmatic:
            return
        self._programmatic = None
        self._refresh_suggestions(event.value)

    def _refresh_suggestions(self, text: str) -> None:
        """按此刻这一行重算候选：能补就摆出来，补不了就收起来。

        收起来时**不动输入框**：一行普通的话不该因为「它不像命令」而被改写。
        """
        popup = self._popup
        if popup is None:
            return
        completion = commands.completion_for(text, self._vocabulary())
        if completion is None:
            popup.close()
        else:
            popup.show(completion)

    @property
    def _answering(self) -> bool:
        """此刻是不是有人在等一句回答（`ask` 用输入框问人）。

        这时候那一行是**回答**，不是提示词、也不是命令——所以补全必须关掉：
        一个以 `/` 开头的回答会被候选表按命令来补，回车也会变成「接受候选」，
        于是人打的那句话被悄悄换成了别的东西。回答原样交出去，一个字都不改。
        """
        waiter = self._answer_waiter
        return waiter is not None and not waiter.done()

    def _candidates(self) -> commands.Completion | None:
        """此刻这一行能补什么。`Tab` 用它在弹出关着的时候决定自己有没有活干。"""
        if self._popup is None or self._answering:
            return None
        return commands.completion_for(self.query_one("#prompt", Input).value, self._vocabulary())

    def _vocabulary(self) -> commands.Vocabulary:
        """补全候选里那些名字。读不到就是空的——少几个候选不是起不来的理由。

        `ctx.models` 是唯一的来源（`providers()` / `adapters()` / `current()`），
        和 provider 选择器读的是同一处：两份名单会长成两个答案，
        而人只会相信屏幕上后来出现的那个。
        """
        models = self._ctx.get("models")
        if models is None:
            return commands.Vocabulary()
        try:
            current = models.current()
            return commands.Vocabulary(
                current_model=str(getattr(current, "model", "") or ""),
                providers=tuple(models.providers()),
                adapters=tuple(models.adapters()),
            )
        except Exception:
            return commands.Vocabulary()

    def _dismiss_suggestions(self) -> None:
        """收起提示。**不清输入框**：关掉一个提示不该顺手改掉人打的东西。"""
        if self._popup is not None:
            self._popup.close()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "prompt":
            return
        text = event.value
        event.input.value = ""
        self._recall = None
        self._dismiss_suggestions()

        if self._answer_waiter is not None and not self._answer_waiter.done():
            # 正在等一个回答：这一行是答案，不是提示词，也不是命令。
            self._answer_waiter.set_result(text)
            return
        if not text.strip():
            return
        self._remember(text)
        self._submit(text)

    def _submit(self, text: str) -> None:
        """一行输入的去处。命令和提示词在这里分开，而且**只在这里**分。"""
        if commands.is_command(text):
            command, argument = commands.split(text)
            if command is None:
                lines = commands.unknown_lines(text)
                self._renderer.notice(lines[0], kind="warn")
                for line in lines[1:]:
                    self._renderer.notice(line, kind="dim")
                return
            self._run_command(command, argument)
            return
        self._start_turn(text)

    def _remember(self, text: str) -> None:
        self._history.append(text)
        del self._history[: max(0, len(self._history) - self._config.history_limit)]

    def _set_prompt(self, text: str) -> None:
        """把一行塞进输入框。历史和补全都用它。

        塞进去的这一下**不算打字**，所以它不重算候选（见 `on_input_changed`）：
        从历史里翻出来的一行不是「正在打的字」，而 `↑`/`↓` 在弹出开着的时候
        是「挑候选」——两个意思抢同一个键，翻历史就会翻不动（按一下 ↑ 弹出开了，
        再按 ↓ 变成挪高亮）。

        想补全翻出来的那一行，按 Tab：它自己会去算候选，见 `action_complete`。
        """
        self._programmatic = text
        prompt = self.query_one("#prompt", Input)
        prompt.value = text
        prompt.cursor_position = len(text)

    # ---------------------------------------------------------------- 命令

    def _run_command(self, command: SlashCommand, argument: str) -> None:
        if command.name == "/help":
            self._write_help()
        elif command.name == "/status":
            self._write_status()
        elif command.name == "/model":
            self.pick_model(argument)
        elif command.name == "/provider":
            # 带参数就是「打开就带着过滤词」。不带参数的行为一个字没变——
            # 这个参数是为了让补全补出来的那段字真的有作用：
            # 补进一个被命令忽略的参数里，比补不出来更让人以为界面坏了。
            self._open_picker(argument)
        elif command.name == "/clear":
            self.action_clear()
        elif command.name == "/quit":
            self.exit()

    def _write_help(self) -> None:
        lines = commands.help_lines(self._key_help())
        self._renderer.notice(lines[0], kind="info")
        for line in lines[1:]:
            self._renderer.notice(line, kind="dim")

    def _write_status(self) -> None:
        """`/status`：状态条上那一行，加上它的细节。

        分行写，而不是挤成一行：状态条上的每一段在终端宽度不够时都会换行，
        与其让它自己随便断，不如这里就断在该断的地方。
        """
        facts = self._status_facts()
        permission = str(facts.pop("permission", "") or "")
        self._renderer.notice(status_line(**facts), kind="info")
        self._renderer.notice(self._model_line(), kind="dim")
        if permission:
            self._renderer.notice(f"权限：{permission}", kind="dim")

    def _model_line(self) -> str:
        choice = self._current_choice()
        if choice is None:
            return "模型：（这次组合里没有 ctx.models）"
        return (
            f"模型：{choice.describe()}  provider={choice.provider or '（无）'}"
            f"  适配器={choice.adapter or '（无）'}"
        )

    def pick_model(self, name: str) -> None:
        """`/model`：不带名字就看现在是谁，带名字就换（provider 不变）。"""
        models = self._ctx.get("models")
        if models is None:
            self._renderer.notice("这次组合里没有 ctx.models，看不了也换不了模型", kind="warn")
            return
        if not name:
            self._renderer.notice(f"当前模型：{_model_label(self._current_choice())}")
            return

        current = self._current_choice()
        if current is None or not current.provider:
            # 回放适配器没有 provider：它按脚本吐字，不认模型名。
            # 说清楚该走哪条路，比让它去报一句「换模型要同时给出 provider 和 model」有用。
            self._renderer.notice(
                f"现在用的是 {_model_label(current)}，它没有 provider；"
                f"先 /provider 选一家，再换模型",
                kind="warn",
            )
            return
        try:
            choice = models.switch(provider=current.provider, model=name)
        except DuGentXError as exc:
            # 换不了不是崩了：把话说清楚，界面继续用着。
            self._renderer.notice(str(exc), kind="error")
            return
        self.choose_model(choice)

    def choose_model(self, choice: ModelChoice | None) -> None:
        """选择器回来（或者 `/model` 换成功）的那一下。

        真正写会话日志、发 `model/switched` 的是 `model_switch.py`——这里只把结果
        立刻显示出来。两边写的是同一个字段、同一个值，所以先显示谁都不影响
        「现在到底在跟谁说」。
        """
        if choice is not None:
            self.update_status(StatusUpdate(model=choice.describe()))

    def _current_choice(self) -> ModelChoice | None:
        models = self._ctx.get("models")
        if models is None:
            return None
        try:
            return models.current()
        except Exception:
            return None

    def _key_help(self) -> list[tuple[str, str]]:
        """界面自己的键位表。`/help` 读它，省得说明和实际键位各写一份、然后分叉。"""
        return [
            (_key_label(str(binding.key)), str(binding.description))
            for binding in self.BINDINGS
            if binding.description
        ]

    def get_system_commands(self, screen: Any) -> Iterable[SystemCommand]:
        """把同一批命令接到 textual 自带的命令面板（Ctrl-P）上。

        面板里点一条和手打一条走的是同一个 `_run_command`——两条路，一份实现。
        """
        yield from super().get_system_commands(screen)
        for command in COMMANDS:
            yield SystemCommand(
                command.palette_title,
                command.summary,
                partial(self._run_command, command, ""),
            )

    # ---------------------------------------------------------------- 动作

    def action_pick_provider(self) -> None:
        """`Ctrl+O` / 命令面板里的那一行：打开选择器，不带过滤词。"""
        self._open_picker("")

    def _open_picker(self, filter: str) -> None:
        models = self._ctx.get("models")
        if models is None:
            self._renderer.notice("这次组合里没有 ctx.models，换不了 provider", kind="warn")
            return
        self.push_screen(
            ProviderPicker(models, current=self._current_choice(), filter=filter),
            self.choose_model,
        )

    # ---------------------------------------------------------------- 补全

    def action_complete(self) -> None:
        """`Tab`：有弹出就接受高亮的那条，没有弹出就把候选叫出来。

        Tab 是「帮我打完」，回车是「就这样，走」。两件事分开，是因为补全完
        还想再看一眼是很常见的（尤其参数），而把它们并起来就再也没有
        「补全但不提交」这个动作了。

        （另一种做法是：只有一条候选才接受、多条就往下挪一格。这里选的是
        「接受」——高亮本来就默认停在第一条上，而一个「Tab 有时补全有时挪动」
        的键，用起来要靠猜。选哪条靠 `↑`/`↓`，这一点写在 `/help` 里。）

        **不做路径补全**（pi 在 Tab 上做的那件）：DugentX 的输入框收的是一句话，
        里面没有任何标记能说明某几个字是路径，所以猜不出该拿什么去匹配——
        一个半准的补全比没有补全更耽误事。理由在 `commands.completion_for`。
        """
        popup = self._popup
        if popup is None:
            return
        if popup.completion is None:
            # 这一行确实有候选，只是弹出还没叫出来（比如刚从历史里翻出来的
            # 一条命令）。按 Tab 就是「给我看看能补什么」。
            self._refresh_suggestions(self.query_one("#prompt", Input).value)
            return
        self._accept_suggestion(submit=False)

    def action_accept_completion(self) -> None:
        """`Enter`：接受高亮的那一条。

        补的是**命令名**时顺手把这一行交出去（补完就等于打完了，再按一次回车
        是多余的）；补的是**参数**时不交——参数补错一个字母就跑掉一条命令，
        比多按一次回车贵。
        """
        self._accept_suggestion(submit=True)

    def _accept_suggestion(self, *, submit: bool) -> None:
        """接受高亮的那一条：改写输入框，然后（命令名的话）把它交出去。

        「交出去」走的是**输入框那条路**（往输入框发一条 `Submitted`），而不是
        在这里另调一次 `_submit`：提交之后要清空输入框、要记进历史、要处理
        「正在等一个回答」——那三件事已经有且只有一处实现，多一条捷径，
        两边的行为迟早会分叉（比如历史里出现一条没被记全的输入）。
        """
        popup = self._popup
        if popup is None or popup.completion is None:
            return
        item = popup.highlighted
        if item is None:
            return
        mode = popup.completion.mode
        prompt = self.query_one("#prompt", Input)
        text = popup.completion.apply(prompt.value, item)
        self._set_prompt(text)
        self._refresh_suggestions(text)
        if submit and mode == "command":
            # 提交的是**去掉补全那个尾随空格**的那一份。那个空格是为了让你接着
            # 打参数（Tab 那条路留着它），回车提交时它没有用，而它会跟着进历史
            # ——下次按上箭头会看见一条多一个空格的「上一条」，看着像上一条，
            # 其实不是。命令本身照旧解析（`split()` 自己会 strip）。
            prompt.post_message(Input.Submitted(prompt, text.strip()))

    def action_clear(self) -> None:
        """`/clear`：只清屏幕上的流水账。

        会话日志一个字都不动——它是唯一真相，屏幕上这一份只是它的一个视图。
        把「清屏」做成「删历史」的话，一次手滑就抹掉了当时到底问了谁、同意了哪一条。
        """
        if self._transcript is not None:
            self._transcript.clear()

    def action_leave(self) -> None:
        """Ctrl-D：输入框是空的时候退出；里面有字就什么都不做（旧习惯的延续）。"""
        if not self.query_one("#prompt", Input).value:
            self.exit()

    def action_cancel_prompt(self) -> None:
        """`Esc`：先收提示，再取消提问，都没有就什么都不做。

        补全开着的时候只收提示，**一个字都不动输入框**：Esc 在这里是
        「我不要这个提示」，不是「我不要我刚打的这行字」——后者要用
        `Ctrl+U` / `Ctrl+K` 这类明确的编辑键。
        """
        if self._popup is not None and self._popup.completion is not None:
            self._dismiss_suggestions()
            return
        waiter = self._answer_waiter
        if waiter is not None and not waiter.done():
            waiter.set_result(None)

    def action_history_previous(self) -> None:
        """`↑`：提示开着就上移高亮，否则翻历史。

        一个键两种意思，但**不会同时需要**：提示开着的时候人在挑候选，
        没开的时候人在找上一条输入。
        """
        if self._popup is not None and self._popup.completion is not None:
            self._popup.move(-1)
            return
        if not self._history:
            return
        index = (
            len(self._history) - 1 if self._recall is None else max(0, self._recall - 1)
        )
        self._recall = index
        self._set_prompt(self._history[index])

    def action_history_next(self) -> None:
        if self._popup is not None and self._popup.completion is not None:
            self._popup.move(1)
            return
        if self._recall is None:
            return
        index = self._recall + 1
        if index >= len(self._history):
            self._recall = None
            self._set_prompt("")
            return
        self._recall = index
        self._set_prompt(self._history[index])

    # ---------------------------------------------------------------- 问人

    async def ask_text(self, question: Question) -> str | None:
        """用**输入框**问一句。返回 None 表示没人回答（Esc、或者界面退出）。

        走输入框而不是另开一个弹窗：问一句用的是这台机器上最自然的那个输入位置，
        不是又一块要学的新界面。等答案的地方只有 `_answer_waiter` 一个，
        所以「这一行是回答」和「这一行是提示词」不会串。
        """
        self._renderer.question(question)
        loop = asyncio.get_running_loop()
        waiter: asyncio.Future[str | None] = loop.create_future()
        self._answer_waiter = waiter
        self._dismiss_suggestions()
        prompt = self.query_one("#prompt", Input)
        prompt.placeholder = question.prompt or "回答"
        prompt.focus()
        try:
            text = await waiter
        finally:
            self._answer_waiter = None
            prompt.placeholder = self._placeholder()
        if text is None:
            self._renderer.answer("（取消）")
            return None
        self._renderer.answer(text)
        return text

    async def choose_key(self, question: Question, choices: tuple[Choice, ...]) -> str | None:
        """弹一个选择。返回按键，取消返回 None。

        回答要回显到流水账里：审批过的东西在屏幕上没有痕迹的话，事后没人说得清
        当时到底同意了哪一条。
        """
        key = await self.push_screen_wait(ChoiceModal(question, choices))
        label = next((choice.label for choice in choices if choice.key == key), "（取消）")
        self._renderer.answer(label if key is not None else "（取消）")
        return key

    # ---------------------------------------------------------------- 回合

    def _start_turn(self, text: str) -> None:
        agent = self._ctx.get("agent")
        if agent is None:
            self._renderer.error("这次组合里没有 agent，界面没东西可跑")
            return
        self.run_worker(self._turn(agent, text), name="turn", exclusive=True)

    async def _turn(self, agent: Any, text: str) -> None:
        self._renderer.user(text)
        try:
            await agent.send(text)
        except Exception as exc:  # 一个回合炸掉不该让整个界面退出
            self._renderer.error(f"{type(exc).__name__}: {exc}")

    def _placeholder(self) -> str:
        return f"{self._config.prompt}{DEFAULT_PLACEHOLDER}"

    # ---------------------------------------------------------------- 两条路

    async def run(self, *, once: str | None = None) -> int:
        """跑起来。`once` 非空时只跑一个回合然后退出（脚本化用）。

        注意 `once` 这条路**不需要界面**：它走 `PlainTui`，输出的是一行行文字。
        界面只在真的有人坐在终端前的时候才起来。退出码：0 正常，1 这个回合炸了，
        2 起不来。
        """
        if once is not None:
            return await self._plain.run(once=once)
        if not self._tty:
            print(
                "dugentx: 这里不是交互式终端，界面起不来；"
                '脚本化请用 dugentx tui --once "..."',
                file=sys.stderr,
            )
            return 2
        await self.run_async()
        return 0
