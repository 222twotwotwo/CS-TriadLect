"""human 缝 —— 人机通道。

这是第 12 个缝，也是「一个编码 TUI 怎么长出来」的答案。

问题很具体：审批必须问人，但**问谁**不该由审批逻辑决定。
以前的写法是 `InteractiveApproval(ask=input)`——审批知道自己在跟 stdin 说话。
一旦界面换成 TUI，这条线就断了：TUI 有它自己的输入行、自己的键位、
自己的重绘节奏，它不可能让审批去调 `input()`。

所以把「跟人说话」这件事本身做成一个缝：

```
permissions 缝  ──依赖──▶  human 缝  ◀──实现──  stdio 通道 / TUI 通道
（决定要不要问）           （只负责问）          （怎么问是它的自由）
```

于是「加一个编码 TUI」不是往 harness 里塞 UI，而是**换上一个新的
`ctx.human` 实现**——和换文件系统、换模型是同一类操作。审批逻辑一行不改。

这条缝故意只认三种动作，因为它们就是「与人交互」的全部原子：

- `ask`    问一句，要一段自由文本
- `choose` 给几个选项，要一个键
- `note`   单向告知，不等回答

`confirm` 那种「审批专用的 yes/no」不在这里——它是 `choose` 的一个用法。
把审批的语义放进这条缝，会让它长成一个审批库；
把它留在 permissions 缝里，两边才各自干净。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

NoteKind = Literal["info", "success", "warn", "error", "dim", "tool"]

YES = "y"
NO = "n"
ALWAYS = "a"
"""`choose` 在审批场景下的三个标准键。

`ALWAYS` 是「这个工具别再问了」——它必须存在。每次都要再按一次 y 的审批，
只会把人训练成不停按 y 的人，那等于没有审批。
"""


@dataclass(slots=True)
class Choice:
    """一个选项。`key` 是调用方拿到的返回值，`label` 给人看。"""

    key: str
    label: str
    is_default: bool = False

    @staticmethod
    def yes() -> Choice:
        return Choice(YES, "允许这一次")

    @staticmethod
    def no() -> Choice:
        return Choice(NO, "拒绝", is_default=True)

    @staticmethod
    def always() -> Choice:
        return Choice(ALWAYS, "本会话内都允许")


APPROVAL_CHOICES: tuple[Choice, ...] = (Choice.yes(), Choice.always(), Choice.no())
"""审批默认给三个选项，按「同意 / 一律同意 / 拒绝」排。"""


@dataclass(slots=True)
class Question:
    """一次提问。通道拿到的就是这些东西，它不必知道是谁在问。"""

    prompt: str
    detail: str = ""
    choices: tuple[Choice, ...] = ()
    """空表示自由文本。非空表示只能在这几个键里选一个。"""

    title: str = ""

    @property
    def is_choice(self) -> bool:
        return bool(self.choices)

    def default_key(self) -> str:
        for choice in self.choices:
            if choice.is_default:
                return choice.key
        return self.choices[0].key if self.choices else ""


@dataclass(slots=True)
class Answer:
    """一次回答。`cancelled` 和「回答了空字符串」是两件事，不能合并。"""

    text: str = ""
    cancelled: bool = False
    source: str = ""
    """谁回答的（stdio / tui / policy），写进事件日志用。"""

    @property
    def key(self) -> str:
        return self.text.strip().lower()

    def __bool__(self) -> bool:
        return not self.cancelled and bool(self.text.strip())


@runtime_checkable
class HumanChannel(Protocol):
    """`ctx.human` —— 此刻这台机器上，人是怎么跟 agent 说话的。"""

    name: str

    def interactive(self) -> bool:
        """现在真的有人能回答吗？

        不是终端一律返回 False。审批据此**直接拒绝**而不是干等——
        一个在 CI 里挂住等输入的 harness，比一个当场说「不」的糟糕得多。
        """
        ...

    async def ask(self, question: Question) -> Answer: ...

    async def choose(self, question: Question) -> Answer: ...

    def note(self, text: str, *, kind: NoteKind = "info") -> None:
        """单向输出一行。不走 async：告知不该让调用方等。"""
        ...
