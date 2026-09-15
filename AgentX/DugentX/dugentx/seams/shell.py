"""shell 缝 —— 执行命令。

和 fs 一样，做成缝是为了让执行世界可搬。本地进程、容器、远程沙箱，
接口只有一个 `run`。

**超时是必须的**：一条 `ping` 或者一个等待输入的交互式命令会把
整个会话挂死。超时不算失败，算一种结果——模型需要知道「它超时了」，
而不是永远等在那里。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


@dataclass(slots=True)
class ShellResult:
    command: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration: float = 0.0
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def render(self, *, head: int = 60, tail: int = 20) -> str:
        """渲染给模型看。

        输出可能非常长，截断规则要**说清楚截了多少**——
        否则模型会以为自己看到了全部，然后在被截掉的中间部分上推理。
        """
        lines: list[str] = [f"$ {self.command}"]
        body = self.stdout.splitlines()
        if len(body) > head + tail:
            omitted = len(body) - head - tail
            body = [*body[:head], f"…（省略 {omitted} 行）…", *body[-tail:]]
        lines.extend(body)
        if self.stderr.strip():
            lines.append("--- stderr ---")
            lines.extend(self.stderr.splitlines()[:head])
        if self.timed_out:
            lines.append(f"（超时，已终止；用时 {self.duration:.1f}s）")
        else:
            lines.append(f"（退出码 {self.exit_code}；用时 {self.duration:.1f}s）")
        return "\n".join(lines)


@runtime_checkable
class Shell(Protocol):
    """`ctx.shell`。"""

    cwd: Path

    async def run(
        self,
        command: str,
        *,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
    ) -> ShellResult: ...


@dataclass(slots=True)
class ShellPolicy:
    """命令白名单/黑名单。

    注意这不是安全边界——真正的边界是操作系统权限和沙箱。
    这一层是**护栏**：拦掉明显不该发生的事，并把「为什么拦」说清楚。
    """

    allow_prefixes: tuple[str, ...] = ()
    deny_substrings: tuple[str, ...] = (
        "rm -rf /",
        "mkfs",
        ":(){:|:&};:",
        "shutdown",
        "diskpart",
    )
    notes: dict[str, str] = field(default_factory=dict)

    def check(self, command: str) -> str | None:
        lowered = command.lower()
        for bad in self.deny_substrings:
            if bad.lower() in lowered:
                return f"命令里出现了被禁止的片段 {bad!r}"
        if self.allow_prefixes and not any(
            command.strip().startswith(p) for p in self.allow_prefixes
        ):
            return f"命令不在白名单里（允许的前缀：{list(self.allow_prefixes)}）"
        return None
