"""human 缝的 stdio provider —— 没有 TUI 时的那个通道。

行为刻意与 TUI 通道**完全一致**，只是长得丑：同样是 `ask` / `choose` / `note`，
同样在不是终端时拒绝而不是干等。差别只在呈现——这让「换通道」这件事
不影响任何一行调用方代码，也让审批逻辑可以在没有 TUI 的环境里被测试。

三条规矩，每条都是为了让审批不被用成复选框：

1. 不是终端就拒绝。在 CI 里挂住等输入的 harness 比当场说「不」的糟糕得多。
2. `a` 记住一整个会话。每次都要再按一次 y，只会训练出不停按 y 的人。
3. 阻塞的 `input()` 走 `asyncio.to_thread`，否则它会把整个事件循环
   连同别的任务一起冻住——一次审批只该挡住那一次工具调用。
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable
from typing import IO

from dugentx.seams.human import ALWAYS, Answer, Choice, NoteKind, Question

_ASK = Callable[[str], str]


class StdioHuman:
    """在标准输入输出上问一句、答一句。"""

    name = "stdio"

    def __init__(
        self,
        *,
        stream: IO[str] | None = None,
        out: IO[str] | None = None,
        ask: _ASK | None = None,
        allow_always: bool = True,
    ) -> None:
        """建一个 stdio 通道。

        `stream` 只用来判断「有没有人」；`out` 是输出；`ask` 是提问函数。
        两个都可注入——不是为了让调用方灵活，而是为了让审批路径**可测**。
        一个只能在真终端上测的审批路径，等于一条没测过的路径。
        """
        self._stream = stream if stream is not None else sys.stdin
        self._out = out if out is not None else sys.stdout
        self._ask = ask if ask is not None else input
        self._allow_always = allow_always
        self._session_allow: set[str] = set()
        self._closed = False

    # ---------------------------------------------------------------- 状态

    def interactive(self) -> bool:
        if self._closed:
            return False
        return bool(getattr(self._stream, "isatty", lambda: False)())

    @property
    def session_allow(self) -> frozenset[str]:
        """本会话里被「一律允许」过的提问标题。"""
        return frozenset(self._session_allow)

    def note(self, text: str, *, kind: NoteKind = "info") -> None:
        if self._closed or not text:
            return
        prefix = {"warn": "! ", "error": "× ", "success": "✓ ", "dim": "  "}.get(kind, "")
        print(f"{prefix}{text}", file=self._out, flush=True)

    # ---------------------------------------------------------------- 提问

    async def ask(self, question: Question) -> Answer:
        if not self.interactive():
            return Answer(cancelled=True, source=self.name)
        line = await self._prompt(self._render(question))
        if line is None:
            return Answer(cancelled=True, source=self.name)
        return Answer(text=line, source=self.name)

    async def choose(self, question: Question) -> Answer:
        """只在给定选项里选一个。空回答取默认项——回车是最常见的输入。"""
        if not self.interactive():
            return Answer(cancelled=True, source=self.name)

        choices = self._effective_choices(question)
        while True:
            raw = await self._prompt(self._render(question, choices))
            if raw is None:
                return Answer(cancelled=True, source=self.name)
            raw = raw.strip().lower()
            if not raw:
                default = question.default_key() or (choices[0].key if choices else "")
                if default:
                    return Answer(text=default, source=self.name)
                continue
            for choice in choices:
                if raw == choice.key.lower():
                    return Answer(text=choice.key, source=self.name)
            self.note(f"只能选：{'、'.join(c.key for c in choices)}", kind="warn")

    # ---------------------------------------------------------------- 内部

    def _effective_choices(self, question: Question) -> tuple[Choice, ...]:
        """记住「一律允许」之后，就不再给那个选项了。

        这不是省事：一个已经决定过的问题再问一遍，只会让这个机制显得随意。
        """
        if question.title in self._session_allow:
            return tuple(c for c in question.choices if c.key != ALWAYS)
        return question.choices

    def _render(self, question: Question, choices: tuple[Choice, ...] | None = None) -> str:
        parts = []
        if question.detail:
            parts.append(question.detail)
        parts.append(question.prompt)
        options = choices if choices is not None else question.choices
        if options:
            shown = "  ".join(
                f"[{c.key}]{c.label}" + ("（默认）" if c.key == question.default_key() else "")
                for c in options
            )
            parts.append("  " + shown)
        return "\n".join(parts) + "\n> "

    async def _prompt(self, text: str) -> str | None:
        """把提示打到**注入的输出流**上，再调注入的输入函数收一行。

        把提示自己打出来、把 `ask` 只当作「读一行」的原语，是为了让输出
        只有一个出口：`note` 和提示都走 `self._out`，测试里抓一个 StringIO
        就能看见用户到底看到了什么。交给 `input()` 去打的话，那部分输出
        永远逃出测试的范围。

        `None` 表示输入结束了（Ctrl-D / Ctrl-C / EOF）。这和「回答了一个空串」
        是两件事：前者是**没回答**，后者是**回答了「空」**。
        """
        if text:
            print(text, end="", file=self._out, flush=True)
        try:
            return await asyncio.to_thread(self._ask, "")
        except (EOFError, KeyboardInterrupt):
            # 关掉输入不该崩掉整个会话：这是一次「没回答」，不是一次故障。
            self._closed = True
            print(file=self._out, flush=True)
            return None

    def remember(self, title: str) -> None:
        if self._allow_always and title:
            self._session_allow.add(title)

    def close(self) -> None:
        self._closed = True
