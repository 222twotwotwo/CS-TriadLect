"""commands —— 输入框里那些以 `/` 开头的话。

它们是**界面自己的命令**，不是发给模型的消息。这条区分必须硬：一句 `/clear`
被当成提示词发出去，模型会一本正经地回答你它没法清屏——那种错很难查，
因为一切看起来都在正常工作。

所以命令在这里登记成一张表：一个名字、一句话说明、要不要参数。`/help`、
命令面板（Ctrl-P）、以及真正派发它的那段代码都读同一张表，不各写一份——
一个只有派发代码认识、而帮助里没写的命令，等于不存在。

**为什么单独一个文件。** 表是纯数据，派发是界面的事。分开之后，
「有哪些命令」这件事不需要起一个终端就能被检查。

补全（输入框下面那个弹出）也长在这张表上：候选就是这里的命令名和别名，
`arg_source` 说明哪条命令的参数能补、补的是哪一类名字。补全的**判断**写在
这里（纯函数，起不起界面都能验），候选里的名字由界面从 `ctx.models` 读出来
传进来——这张表不认识上下文，也不该认识。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from dugentx.tui.fuzzy import rank

PROMPT = "/"

ARG_SOURCES = ("models", "providers")
"""`arg_source` 认得的取值。写错一个字的后果是那条命令的参数静默地补不了，
所以装载期就检查（见下面的断言），而不是等谁发现「/provider 怎么不提示」。"""


@dataclass(frozen=True, slots=True)
class Vocabulary:
    """补全候选里那些**名字**。界面从 `ctx.models` 读出来，命令表不自己去读。

    三项都可能为空（没有 llm 插件时就是这样），空的那些只是少几个候选，
    不是补不了。`current_model` 排在第一位：`/model` 补的就是「现在这个模型名」，
    而弹出里第一条就是 Tab 会替你按下去的那一条。
    """

    current_model: str = ""
    providers: tuple[str, ...] = ()
    adapters: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SlashCommand:
    """一条命令。`keys` 是等价的键位，没有就是空串。"""

    name: str
    summary: str
    arg: str = ""
    keys: str = ""
    aliases: tuple[str, ...] = ()
    arg_source: str = ""
    """这条命令的参数补什么名字（`Vocabulary` 里的一项）；空就是没得补。"""

    @property
    def usage(self) -> str:
        """写给人看的用法，比如 `/model [名字]`。"""
        return f"{self.name} {self.arg}".strip()

    @property
    def palette_title(self) -> str:
        """命令面板里那一行的标题。带上用法，因为面板里没有别的地方写它。"""
        return self.usage


COMMANDS: tuple[SlashCommand, ...] = (
    SlashCommand("/help", "列出这些命令和键位", keys="F1"),
    SlashCommand("/status", "看模型、会话、步数、tokens、权限档位"),
    SlashCommand(
        "/model", "看当前模型；带名字就换（provider 不变）", arg="[名字]", arg_source="models"
    ),
    SlashCommand(
        "/provider",
        "打开 provider 选择器，也可以切回适配器",
        arg="[名字]",
        keys="Ctrl-O",
        arg_source="providers",
    ),
    SlashCommand("/clear", "清空屏幕上的流水账（会话日志不动）", keys="Ctrl-L"),
    SlashCommand("/quit", "退出", aliases=("/exit",), keys="Ctrl-Q"),
)
"""全部命令。

顺序就是 `/help` 里的顺序：先看、再改、最后走。补全弹出也按这个顺序排
（同分时保持表里的次序），所以「先看、再改、最后走」在那张弹出里同样成立。
"""

for _command in COMMANDS:
    assert not _command.arg_source or _command.arg_source in ARG_SOURCES, (
        f"{_command.name} 的 arg_source={_command.arg_source!r} 不认识；"
        f"可用：{ARG_SOURCES}"
    )

_BY_NAME: dict[str, SlashCommand] = {}
for _command in COMMANDS:
    for _name in (_command.name, *_command.aliases):
        _BY_NAME[_name] = _command


def is_command(text: str) -> bool:
    """这一行是不是一条命令。前导空白不算：手滑多打一个空格仍然该是命令。"""
    return text.strip().startswith(PROMPT)


def split(text: str) -> tuple[SlashCommand | None, str]:
    """把一行拆成（命令，参数）。

    认不出来就是 `(None, "")` —— 调用方据此说一句「没有这条命令」，
    而不是把 `/随便什么` 当成提示词发给模型。**不认识的命令不能悄悄变成
    用户的话**：那不是宽容，那是不打招呼地改变了一次请求的内容。
    """
    stripped = text.strip()
    if not stripped.startswith(PROMPT):
        return None, ""
    head, _, rest = stripped.partition(" ")
    command = _BY_NAME.get(_normalize(head))
    if command is None:
        return None, ""
    return command, rest.strip()


def lookup(name: str) -> SlashCommand | None:
    """按名字找命令，别名也算。大小写不敏感——手快时没人会去按 shift。"""
    return _BY_NAME.get(_normalize(name))


def known_names() -> list[str]:
    """认得的所有写法，按表的顺序。写「你可能是想打这几个」时用它。"""
    return [name for command in COMMANDS for name in (command.name, *command.aliases)]


def help_lines(keys: Iterable[tuple[str, str]] = ()) -> list[str]:
    """`/help` 要印的那些行。

    键位由调用方给——它就是界面的 `BINDINGS`。让界面自己报出来，省得说明
    和实际键位各写一份、然后慢慢分叉；一份对不上的键位表比没有更糟。

    不做列对齐：这是中文界面，一个汉字占两列，按字符数补空格看起来是歪的，
    而按显示宽度补又要在这一层引入一套宽度计算——为了一行说明，不值。
    """
    lines = ["命令（以 / 开头；它们由界面处理，不会发给模型）："]
    for command in COMMANDS:
        extra = f"（{command.keys}）" if command.keys else ""
        alias = f"，也可以写 {' / '.join(command.aliases)}" if command.aliases else ""
        lines.append(f"  {command.usage} — {command.summary}{extra}{alias}")
    key_lines = [f"  {key} — {description}" for key, description in keys]
    if key_lines:
        lines.append("键位：")
        lines.extend(key_lines)
    return lines


def unknown_lines(text: str) -> list[str]:
    """一条不认识的命令：说清楚它是什么，再列出认得的那些。"""
    return [
        f"没有这条命令：{text.strip()}",
        "认得的只有这些：" + "、".join(known_names()),
        "想看每一条的说明就打 /help。",
    ]


def _normalize(name: str) -> str:
    text = name.strip().lower()
    if not text.startswith(PROMPT):
        text = PROMPT + text
    return text


# ---------------------------------------------------------------------- 补全


@dataclass(frozen=True, slots=True)
class Suggestion:
    """弹出里的一行。`value` 是接受之后填进输入框的那一段，`label` 是行首那一段。"""

    value: str
    label: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class Completion:
    """此刻能补的那一批：补的是哪一段（`start` + `query`），以及有哪些候选。

    `mode` 不是装饰，它决定接受之后**要不要就此把这一行交出去**：

    - `command`：补的是命令名。接受之后行就完整了，所以回车顺手把它跑掉
      （pi 也是这么分的：命令名接受完直接落到提交上）。
    - `argument`：补的是参数。接受之后还该让人看一眼再回车——参数补错一个字母
      就跑掉一条命令，比多按一次回车贵。
    """

    mode: str
    start: int
    query: str
    items: tuple[Suggestion, ...]

    def apply(self, text: str, item: Suggestion) -> str:
        """接受一条：把正在打的这一段换成它，行的其余部分一个字不动。"""
        head = text[: self.start]
        tail = text[self.start + len(self.query) :].lstrip()
        if self.mode == "argument":
            return head + item.value + tail
        # 命令名后面留一个空格：命令多半还欠一个参数，而多一个空格不影响
        # `split()`（它自己会 strip）——留着空格，下一个字直接就是参数。
        return f"{head}{item.value} {tail}" if tail else f"{head}{item.value} "


def completion_for(text: str, vocabulary: Vocabulary | None = None) -> Completion | None:
    """这一行此刻能补什么。补不了就是 `None`——弹出据此消失。

    只在「还像一条命令」的时候给候选：开头是 `/`（前导空白不算，手滑多打一个
    空格仍然是命令）、而且还没打完命令名；打完命令名之后，只有声明了
    `arg_source` 的那几条才继续给参数的候选。一行普通的话一个候选都没有，
    所以那个弹出不会在写提示词的时候冒出来。

    **不做路径补全**（pi 在 Tab 上做的那件）。pi 的输入框里有 `@文件` 这种明确的
    标记，所以「这一段是不是一个路径」有答案；DugentX 的输入框收的是一句话，
    没有任何标记能说明某几个字是路径——猜出来的候选只会是一个半准的补全，
    而半准的补全比没有补全更耽误事。
    """
    lead = len(text) - len(text.lstrip())
    body = text[lead:]
    if not body.startswith(PROMPT):
        return None

    head, sep, rest = body.partition(" ")
    if not sep:
        items = _command_items(head)
        if not items or _settled(head, items):
            return None
        return Completion(mode="command", start=lead, query=head, items=items)

    command = lookup(head)
    if command is None or not command.arg_source:
        return None
    items = _argument_items(command.arg_source, rest, vocabulary or Vocabulary())
    if not items or _settled(rest, items):
        return None
    return Completion(
        mode="argument",
        start=lead + len(head) + 1,
        query=rest,
        items=items,
    )


def _settled(query: str, items: tuple[Suggestion, ...]) -> bool:
    """已经打完了：正在打的那一段和某个候选一字不差。

    这种时候不给候选。一个和输入框里一模一样的候选不是候选，是回声——而它会
    把回车卡住：弹出开着的时候回车是「接受」，接受一个没有变化的值等于什么都没做，
    于是那条命令再也跑不起来（按几次回车都一样）。这不是假设：它是本文件
    那段测试先撞上的。
    """
    text = query.strip().lstrip(PROMPT).lower()
    if not text:
        return False
    return any(item.value.lstrip(PROMPT).lower() == text for item in items)


def _command_items(token: str) -> tuple[Suggestion, ...]:
    """候选是命令表里认得的每一个写法，别名也算。

    行首写的是**用法**（`/model [名字]`），不是光一个名字：那个 `[名字]` 就是
    「这条命令还欠一个参数」，而那是打之前最该知道的一件事。填进去的仍然是名字
    （`Suggestion.value`），用法只出现在屏幕上。

    别名单独成一条，而不是藏在主名字的说明里：打 `ex` 的人要看到的那一行是
    `/exit`，否则他会以为没补到。
    """
    query = token[len(PROMPT) :]
    candidates: list[Suggestion] = []
    for command in COMMANDS:
        for name in (command.name, *command.aliases):
            if name == command.name:
                candidates.append(
                    Suggestion(value=name, label=command.usage, detail=command.summary)
                )
            else:
                candidates.append(
                    Suggestion(
                        value=name,
                        label=name,
                        detail=f"{command.name} 的别名：{command.summary}",
                    )
                )
    return tuple(rank(candidates, query, key=lambda item: item.label))


def _argument_items(
    source: str, query: str, vocabulary: Vocabulary
) -> tuple[Suggestion, ...]:
    """参数补什么。名字由界面从 `ctx.models` 读出来，这里的顺序就是弹出的顺序。

    `/provider` 给的**正好是选择器列的那些**（provider 加已注册的适配器）：
    补全和选择器要是两份名单，就会有两个「可选的东西」的答案，而人只会相信
    屏幕上后来出现的那个。

    `/model` 给的是**现在这个模型名**，不是 provider 名单。原因是 `/model`
    干的事是「provider 不变、换模型名」，所以往它后面填一个 provider 名，
    等于填了一个命令会当成模型名去用的值——一个补出来的错值，比补不出来糟。
    """
    candidates: list[Suggestion] = []
    if source == "providers":
        candidates.extend(
            Suggestion(value=name, label=name, detail="provider")
            for name in vocabulary.providers
        )
        candidates.extend(
            Suggestion(value=name, label=name, detail="已注册的适配器（可以切回去）")
            for name in vocabulary.adapters
        )
    elif source == "models":
        if vocabulary.current_model:
            candidates.append(
                Suggestion(
                    value=vocabulary.current_model,
                    label=vocabulary.current_model,
                    detail="当前模型",
                )
            )
    return tuple(rank(candidates, query, key=lambda item: item.label))
