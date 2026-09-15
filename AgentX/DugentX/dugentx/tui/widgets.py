"""widgets —— 状态条、补全弹出、provider 选择器、审批弹窗。

这几个都是**界面自己的**东西：它们读 `ctx` 上的服务（`ctx.models`、`ctx.permissions`），
但不接管任何 harness 的逻辑。provider 选择器最后只是调一次 `ctx.models.switch()`；
审批弹窗最后只是把一个按键还给 `ctx.human`。换模型、记住「本会话内都允许」这些
决定都留在缝的那一侧——界面要是自己实现一遍，就一定会有第二种语义。

一句话：这里的文件可以整个删掉，harness 一行不用改。
"""

from __future__ import annotations

import contextlib
from time import monotonic
from typing import TYPE_CHECKING, Any, ClassVar

from rich.markup import escape
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.events import Key
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widgets import Button, Input, OptionList, Static
from textual.widgets.option_list import Option

from dugentx.kernel.errors import DuGentXError
from dugentx.seams.human import Choice, Question
from dugentx.tui.commands import Completion, Suggestion
from dugentx.tui.formatting import (
    SPINNER_INTERVAL,
    STATUS_IDLE,
    StatusUpdate,
    busy_text,
    status_line,
)
from dugentx.tui.fuzzy import filter_names

if TYPE_CHECKING:
    from dugentx.providers.model_switch import ModelChoice

PROVIDER_PREFIX = "provider:"
ADAPTER_PREFIX = "adapter:"

SUGGESTION_ROWS = 8
"""候选最多显示几条。跟着终端高度缩放（见 `SuggestionPopup._limit`），
这是一个上限：命令一共才六条，参数补全最长的名单是 provider 那五十几个——
而「边打边收窄」比「一次看五十条」有用。"""

_PICK_MARK = "›"
"""高亮那一条前面的记号。和输入框的提示前缀用同一个字形：都是「你现在在这里」。"""


# ---------------------------------------------------------------------- 状态条


class StatusBar(Static):
    """一行状态：模型、会话、步数、累计 tokens、权限档位 —— 外加「正在跑」。

    **事实只被事件推着更新**（见 `paint.py`），没有定时器：一个每 200 毫秒重画
    一次的状态条，会和真正发生的事脱钩——你分不清「它变了」和「它只是又刷了
    一下」。模型名、会话、步数、tokens、权限档位这五项属于这一边，它们
    只在真的有事情发生时才变。

    **但「这一步已经跑了 4.2 秒」不是编的，它是一个钟的事实。** 所以这里有一个
    定时器，而它的权力被限死：只在回合真的在跑的那段时间里转（`paint.py` 用
    `turn/start` / `turn/end` 开关它，见 `StatusUpdate.busy`），回合结束就停、
    界面退场就停。换句话说是两句话而不是一句：**事实归事件，时间归钟。**
    原来那条「不要定时器」的理由没有被推翻，只是被划清了边界——它防的是
    「用一个心跳去**伪造**变化」，而一个正在跑的回合本来就在变。

    `animate=False` 时整个动效关掉：不转圈、不读表，只留一个静止的字形。
    有些场合（录屏、日志回放、单纯不喜欢动的东西）动效是干扰，而
    「我正在干活」这件事不需要靠动来说。
    """

    DEFAULT_CSS = """
    StatusBar {
        height: auto;
        max-height: 3;
        padding: 0 1;
        background: $panel;
        color: $text;
    }
    """

    def __init__(self, *, animate: bool = True, **kwargs: Any) -> None:
        super().__init__("", **kwargs)
        self._animate = animate
        self._state: dict[str, Any] = {
            "model": "",
            "session_id": "",
            "steps": 0,
            "tokens": 0,
            "permission": "",
        }
        self._frame = 0
        self._busy_since: float | None = None
        self._timer: Timer | None = None
        self.update(self._line())

    # ---------------------------------------------------------------- 事实

    def update_status(self, update: StatusUpdate) -> None:
        """收下一次局部更新，只改它带来的那几项。

        `busy` 不在 `_state` 里：它不是一个要显示出来的事实，它是「时间那一半
        现在算不算」。把它混进事实里，`/status` 就会开始印一个和事实无关的状态。
        """
        for field in ("model", "session_id", "steps", "tokens", "permission"):
            value = getattr(update, field)
            if value is not None:
                self._state[field] = value
        if update.busy is not None:
            self._set_busy(update.busy)
        self.update(self._line())

    @property
    def state(self) -> dict[str, Any]:
        """此刻状态条上写着的那些事实。测试用它，比去解析一行文字稳。"""
        return dict(self._state)

    @property
    def busy(self) -> bool:
        """此刻有没有活干。它决定状态条最前面那一截是什么。"""
        return self._busy_since is not None

    @property
    def elapsed(self) -> float | None:
        """这一回合已经跑了多少秒。没在跑就是 `None`。"""
        if self._busy_since is None:
            return None
        return monotonic() - self._busy_since

    # ---------------------------------------------------------------- 表

    def _set_busy(self, busy: bool) -> None:
        if busy == self.busy:
            return
        if not busy:
            self._stop_busy()
            return
        self._busy_since = monotonic()
        if not self._animate:
            return
        if self._timer is None:
            self._timer = self.set_interval(SPINNER_INTERVAL, self._tick)
        else:
            self._timer.resume()

    def _stop_busy(self) -> None:
        """停表。定时器**留着**（只是暂停）：一个回合接着一个回合的时候，
        每回合新建一个再扔掉一个，多出来的是一堆生命周期，不是一个定时器。"""
        self._busy_since = None
        if self._timer is not None:
            self._timer.pause()

    def _tick(self) -> None:
        """定时器唯一被允许做的事：往前走一帧，重画这一行。"""
        self._frame += 1
        self.update(self._line())

    def on_unmount(self) -> None:
        """界面退场：表必须停。

        不停的话，一个还在跑的定时器会跟着事件循环一起悬在那里——界面都没了，
        还有东西在替它数秒，而且它会把进程留着不退出（这是真的会发生的那种
        「关不掉」，不是理论问题）。
        """
        self._stop_busy()
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    def _line(self) -> str:
        """这一刻要显示的那一行：状态字形 + 事实。

        字形那一截是钟读出来的（转几圈了、跑了多久），后面那一截是事件带来的。
        两截拼在一行上，但它们各自的来源没有混。
        """
        facts = status_line(**self._state)
        elapsed = self.elapsed
        if elapsed is None:
            return f"{STATUS_IDLE} {facts}"
        return f"{busy_text(frame=self._frame, elapsed=elapsed, animate=self._animate)}  {facts}"


# ---------------------------------------------------------------------- 补全


class SuggestionPopup(Static):
    """输入框上面那个候选列表：打 `/` 的时候自己冒出来，打不成命令就自己消失。

    **它不拿焦点。** 输入框还是输入框，`↑`/`↓`/`Tab`/`Enter`/`Esc` 全由应用
    统一处理（见 `app.py` 的 `check_action`）。一个抢走焦点的弹出会让你在选之前
    先按一次 Tab 回到输入框——而 Tab 在别处正是「补全」，两个意思会打架。

    一行分成两段：**要填进去的那一段**，和**它是谁**。两段都必要——`/provider`
    和 `/model` 的候选里都可能有 `replay`，只有后一段能让人看出它们不是同一件事。
    """

    DEFAULT_CSS = """
    SuggestionPopup {
        height: auto;
        max-height: 8;
        padding: 0 1;
        background: $panel;
        color: $text;
        display: none;
    }
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__("", **kwargs)
        self._completion: Completion | None = None
        self._index = 0
        self._drawn = Text()

    # ---------------------------------------------------------------- 状态

    @property
    def completion(self) -> Completion | None:
        """此刻在补什么。`None` 就是收着的。"""
        return self._completion

    @property
    def highlighted(self) -> Suggestion | None:
        """高亮的那一条。`Tab` / `Enter` 接受的就是它。"""
        items = self.items()
        return items[self._index] if items else None

    def items(self) -> tuple[Suggestion, ...]:
        """此刻给的候选，按弹出的顺序。"""
        return () if self._completion is None else self._completion.items

    @property
    def lines(self) -> list[str]:
        """此刻画出来的每一行（纯文本）。

        和屏幕上是同一份（`_paint` 只在这一处动内容），所以测试读它等于读画面，
        而不是读一个「本该画成这样」的中间值。
        """
        return self._drawn.plain.splitlines()

    # ---------------------------------------------------------------- 开合

    def show(self, completion: Completion) -> None:
        """摆出这一批候选。**第一条默认高亮**：打完直接 Tab 是最自然的用法，
        而「什么都没高亮」会让那个 Tab 静悄悄地没有反应。"""
        self._completion = completion
        self._index = 0
        self.display = True
        self._paint()

    def close(self) -> None:
        """收起来。**一个字都不动输入框里的东西**——关掉一个提示不该顺手改掉
        人打了一半的句子，所以这里和 `app._dismiss_suggestions` 都不碰输入框。"""
        self._completion = None
        self._index = 0
        self.display = False
        self._paint()

    def move(self, delta: int) -> None:
        """上/下移动高亮，到头绕回另一头。"""
        total = len(self.items())
        if total:
            self._index = (self._index + delta) % total
            self._paint()

    # ---------------------------------------------------------------- 画

    def rows(self) -> tuple[Suggestion, ...]:
        """此刻真正画出来的那几条。

        高亮可能落在窗口外（候选比屏幕高），所以窗口跟着高亮走——一个
        「选中了第七条、屏幕上是前八条」的列表，看起来就像按了没反应。
        """
        items = self.items()
        limit = self._limit()
        if len(items) <= limit:
            return items
        start = min(max(0, self._index - limit + 1), len(items) - limit)
        return items[start : start + limit]

    def _limit(self) -> int:
        """最多显示几行：3..8，按终端高度取。

        矮终端里一张吃掉半屏的列表会把上面正在跑的东西挤没，所以跟着高度缩；
        高终端里也封顶——一个几十条的列表，比「边打边收窄」更难看懂。
        """
        height = 40
        with contextlib.suppress(Exception):
            # 没挂上应用的时候（直接构造一个来单测）拿不到高度，用默认那一档。
            height = int(self.app.size.height)
        return max(3, min(SUGGESTION_ROWS, height // 4))

    def _paint(self) -> None:
        """把此刻该显示的东西摆上去。只有这一个地方动内容。"""
        self._drawn = self._build()
        self.update(self._drawn)

    def _build(self) -> Text:
        """画出来。

        用 `Text` 一段段拼，而不是拼一个 rich 标记字符串：候选里出现方括号是
        常事（`/model [名字]` 就是），拿字符串去喂 rich，那些方括号会被当成
        样式标签——屏幕上少几个字，而且只在特定命令上才发生。
        """
        body = Text()
        highlighted = self.highlighted
        for row, item in enumerate(self.rows()):
            if row:
                body.append("\n")
            current = item is highlighted
            body.append(f"{_PICK_MARK} " if current else "  ")
            body.append(item.label, style="bold" if current else "")
            if item.detail:
                body.append(f"  {item.detail}", style="dim")
        return body



# ------------------------------------------------------------------ 选择器

_PICKER_CSS = """
ProviderPicker {
    align: center middle;
}
#picker {
    width: 72;
    max-width: 96%;
    height: auto;
    max-height: 80%;
    border: round $primary;
    background: $surface;
    padding: 0 1;
}
#picker-title { height: auto; }
#picker-filter { border: none; }
#picker-options { height: auto; max-height: 14; }
#picker-model { border: none; display: none; }
#picker-error { height: auto; }
"""


class ProviderPicker(ModalScreen["ModelChoice | None"]):
    """换 provider、换模型，或者退回一个已经注册的适配器。

    三种目标放在同一个列表里，因为它们是同一个问题的三种答案：接下来跟谁说。
    列表分两段——上面是 any-llm 认得的 provider（52 个，离线就能列出来），
    下面是**已经注册在 `ctx.llm` 里的适配器**（比如 `replay`）。第二段是
    「回得去」这件事的全部实现：没有它，一旦换成真 provider 就回不到离线模式了，
    而那正是最需要能回去的时候。

    选中 provider 之后要再给一个模型名：provider 和 model 缺一不可——
    只换 provider 而不换模型名，会拿新家去请求旧家的模型，报出来的错看着像
    「这家不支持这个模型」，其实是我们自己没把状态改干净（`model_switch.py`
    里那段注释说的就是这件事）。

    provider 认不出、模型名为空、key 的环境变量没配……这些都是 `switch()` 在
    动手**之前**抛出来的 `PluginError`。它们显示在这个弹窗里，弹窗不关，
    界面不崩：一个因为 key 没配就整屏退出的选择器，只会让人以为程序坏了。
    """

    CSS = _PICKER_CSS
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "cancel", "取消", show=False),
        # 上下键在输入框里也能用：过滤和挑选本来就是一件事的两半，
        # 让它们分在两个焦点里，等于逼人多按一次 Tab。
        Binding("down", "next_option", "下一条", show=False),
        Binding("up", "previous_option", "上一条", show=False),
    ]

    def __init__(
        self,
        models: Any,
        *,
        current: ModelChoice | None = None,
        filter: str = "",
    ) -> None:
        super().__init__()
        self._models = models
        self._current = current
        self._filter = filter
        self._mode = "provider"
        self._provider = ""

    # ---------------------------------------------------------------- 装配

    def compose(self) -> ComposeResult:
        with Vertical(id="picker"):
            yield Static("", id="picker-title")
            yield Input(placeholder="打字过滤…", id="picker-filter")
            yield OptionList(id="picker-options")
            yield Input(placeholder="模型名，例如 deepseek-chat", id="picker-model")
            yield Static("", id="picker-error")

    def on_mount(self) -> None:
        # 子控件的挂载是**分层**的：屏幕挂载的那一刻，容器里的东西可能还没轮到。
        # 所以初始化推一拍再做——它做的事和时机无关，而抢在挂载中间做会拿到
        # 一个还没有孩子的弹窗（那是真的会炸的，不是理论问题）。
        self.call_after_refresh(self._prepare)

    def _prepare(self) -> None:
        self._title().update(self._headline())
        if self._filter:
            # `/provider mistral` 的那个参数：打开就带着过滤词。
            # 不带的话，补出来的那段字会进一个被忽略的参数里——一个补进去
            # 却没有作用的词，比补不出来更容易让人以为界面坏了。
            self._filter_input().value = self._filter
        self._fill_options()
        self._filter_input().focus()

    # ---------------------------------------------------------------- 小工具

    def _title(self) -> Static:
        return self.query_one("#picker-title", Static)

    def _options(self) -> OptionList:
        return self.query_one("#picker-options", OptionList)

    def _filter_input(self) -> Input:
        return self.query_one("#picker-filter", Input)

    @property
    def mode(self) -> str:
        """此刻在选什么：`provider` 还是 `model`。"""
        return self._mode

    def _headline(self) -> str:
        where = self._current.describe() if self._current is not None else "（还没配）"
        return f"现在：{where}\n换 provider：打字过滤，回车选中；Esc 取消"

    def _provider_names(self) -> list[str]:
        with contextlib.suppress(DuGentXError):
            return list(self._models.providers())
        return []

    def _adapter_names(self) -> list[str]:
        with contextlib.suppress(DuGentXError):
            return list(self._models.adapters())
        return []

    def _fill_options(self) -> None:
        needle = self._filter_input().value
        providers = filter_names(self._provider_names(), needle)
        adapters = filter_names(self._adapter_names(), needle)
        listing: list[Option] = [Option(name, id=f"{PROVIDER_PREFIX}{name}") for name in providers]
        if adapters:
            # 分隔行是禁用的：它是一个标题，不是可选项。做成可选项的话，
            # 回车会选中一个什么都不做的行。
            listing.append(Option("—— 已经注册的适配器（可以切回去）——", disabled=True))
            listing.extend(Option(name, id=f"{ADAPTER_PREFIX}{name}") for name in adapters)
        if not listing:
            listing.append(Option("没有匹配的名字", disabled=True))

        options = self._options()
        options.set_options(listing)
        first = next((index for index, option in enumerate(listing) if option.id), None)
        if first is not None:
            # 第一项默认选中：打完过滤词直接回车是最自然的用法，
            # 而「什么都没高亮」会让那个回车静悄悄地什么都不做。
            options.highlighted = first

    def _move(self, delta: int) -> None:
        """上下移动高亮，跳过禁用的标题行。"""
        options = self._options()
        count = options.option_count
        if not count:
            return
        current = options.highlighted if options.highlighted is not None else 0
        for step in range(1, count + 1):
            index = (current + step * delta) % count
            if not options.get_option_at_index(index).disabled:
                options.highlighted = index
                return

    def _show_error(self, text: str) -> None:
        self.query_one("#picker-error", Static).update(f"[bold red]{escape(text)}[/]")

    def _clear_error(self) -> None:
        self.query_one("#picker-error", Static).update("")

    # ---------------------------------------------------------------- 交互

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "picker-filter":
            self._clear_error()
            self._fill_options()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "picker-model":
            self._apply_model()
            return
        # 过滤框里按回车 = 选中高亮的那一条。列表就在下面，鼠标和键盘
        # 不该有两套「选中」的含义。
        highlighted = self._options().highlighted_option
        self._pick(None if highlighted is None else highlighted.id)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self._pick(event.option_id)

    def _pick(self, option_id: str | None) -> None:
        """选中一条：provider 还要再问一句模型名，适配器直接切。"""
        if not option_id:
            return
        self._clear_error()
        kind, _, value = option_id.partition(":")
        if kind == "adapter":
            self._use_adapter(value)
        elif kind == "provider":
            self._ask_model(value)

    def _use_adapter(self, name: str) -> None:
        """切到一个**已经注册**的适配器，不新建。模型名保持不动。"""
        try:
            choice = self._models.use(name)
        except DuGentXError as exc:
            self._show_error(str(exc))
            return
        self.dismiss(choice)

    def _ask_model(self, provider: str) -> None:
        self._provider = provider
        self._mode = "model"
        self._options().display = False
        self._filter_input().display = False
        model_input = self.query_one("#picker-model", Input)
        model_input.display = True
        model_input.value = ""
        model_input.focus()
        self._title().update(f"{provider} 用哪个模型？打完回车确认；Esc 回到列表")

    def _apply_model(self) -> None:
        model = self.query_one("#picker-model", Input).value.strip()
        if not model:
            self._show_error("模型名不能是空的")
            return
        try:
            choice = self._models.switch(provider=self._provider, model=model)
        except DuGentXError as exc:
            # 错误留在弹窗里，弹窗不关：key 没配是**这一件事**没做成，
            # 不是这个界面坏了。改个名字再试一次就行。
            self._show_error(str(exc))
            return
        self.dismiss(choice)

    def action_next_option(self) -> None:
        self._move(1)

    def action_previous_option(self) -> None:
        self._move(-1)

    def action_cancel(self) -> None:
        """Esc：在选模型就退回列表，在列表上就整个退出。"""
        if self._mode == "model":
            self._mode = "provider"
            self.query_one("#picker-model", Input).display = False
            self._options().display = True
            self._filter_input().display = True
            self._filter_input().focus()
            self._clear_error()
            self._title().update(self._headline())
            return
        self.dismiss(None)


# ------------------------------------------------------------------ 审批弹窗

_CHOICE_CSS = """
ChoiceModal {
    align: center middle;
}
#choice {
    width: 72;
    max-width: 96%;
    height: auto;
    border: round $warning;
    background: $surface;
    padding: 1 2;
}
#choice-detail { color: $text-muted; }
#choice-buttons { height: auto; align-horizontal: left; }
#choice-buttons Button { margin: 0 2 0 0; }
"""


class ChoiceModal(ModalScreen["str | None"]):
    """一次三选一。默认项默认是**选中的那个**，所以回车 = 默认项。

    审批给的默认项是「拒绝」：连按回车不该不小心把写操作放过去，要放行得按对
    一个字母。误拒的代价是再敲一次，误允的代价不是。

    一个键就够，是因为选项之间互斥——终端上按 y 再按回车，那个回车是纯浪费。
    字母键和按钮走的是同一条路（`dismiss(choice.key)`），所以「按 y」和
    「点允许」不可能给出两种答案。
    """

    CSS = _CHOICE_CSS
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "cancel", "取消", show=False)
    ]

    def __init__(self, question: Question, choices: tuple[Choice, ...]) -> None:
        super().__init__()
        self.question = question
        self.choices = choices

    def compose(self) -> ComposeResult:
        default = self.question.default_key()
        with Vertical(id="choice"):
            yield Static(f"[ask] {self.question.prompt or '（空问题）'}", markup=False)
            if self.question.detail:
                yield Static(self.question.detail, id="choice-detail", markup=False)
            with Horizontal(id="choice-buttons"):
                for choice in self.choices:
                    label = f"[{choice.key}] {choice.label}"
                    if choice.key == default:
                        label += "（默认）"
                    yield Button(
                        label,
                        id=f"choice-{choice.key}",
                        variant="primary" if choice.key == default else "default",
                    )

    def on_mount(self) -> None:
        # 同上：按钮在容器里，属于下一层，挂载的那一刻还不在。焦点必须落在
        # 默认项上——不然回车就没有确定的意思了，而那正是这个弹窗的全部意义。
        self.call_after_refresh(self.focus_default)

    def focus_default(self) -> None:
        """把焦点放到默认项上。默认项是「拒绝」，所以回车通向最保守的答案。"""
        self.query_one(f"#choice-{self.question.default_key()}", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(_key_of(event.button.id))

    def on_key(self, event: Key) -> None:
        for choice in self.choices:
            if event.key == choice.key.lower():
                event.stop()
                self.dismiss(choice.key)
                return

    def action_cancel(self) -> None:
        """Esc = 没回答。（`cancelled` 和「回答了一个空串」是两件事。）"""
        self.dismiss(None)


def _key_of(button_id: str | None) -> str:
    prefix = "choice-"
    text = button_id or ""
    return text[len(prefix) :] if text.startswith(prefix) else text
