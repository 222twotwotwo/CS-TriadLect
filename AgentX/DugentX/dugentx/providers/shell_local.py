"""本地 shell —— `shell` 缝落到 `asyncio.create_subprocess_shell` 上的那一份。

三件必须做对的事，每一件都对应一种真实的挂死或失控：

1. **超时是结果，不是失败。** 一条等输入的交互式命令会把整个会话吊住。
   到点后杀的是**整棵进程树**，不是那个 shell：真正的活儿在它的子进程里，
   只杀 shell 会留下孤儿进程继续跑（Windows 上还会继续占着管道，
   于是你连输出都收不到 EOF）。
2. **输出要封顶。** 一条 `yes` 一秒能产出 GB 级输出。上限之外的字节丢掉，
   但**继续读**——不读的话管道写满，进程卡在 write 上永远不退出，
   「限制输出」就变成了「挂死」。截断的事实会写进返回的文本里，
   否则模型会以为它看到的输出是完整的。
3. **拒绝也是一条结果。** 命令被 `ShellPolicy` 挡下时返回一条失败的
   `ShellResult`，而不是抛异常：模型需要看见「谁、为什么不让跑」，
   然后自己换个做法——这才是它能继续工作的方式。

平台差异只有一处：杀进程树。其余全是标准库。

一个 Windows 上的注意事项：`create_subprocess_shell` 需要 Proactor 事件循环
（Windows 上的默认策略），换成 Selector 策略会直接抛 NotImplementedError。
"""

from __future__ import annotations

import asyncio
import contextlib
import locale
import os
import signal
import time
from asyncio.subprocess import DEVNULL, PIPE
from pathlib import Path
from typing import Any

from dugentx.kernel.errors import ToolError
from dugentx.seams.shell import ShellPolicy, ShellResult

DEFAULT_TIMEOUT = 60.0
"""默认超时（秒）。一定要有一个默认值——没有它会挂。"""

DEFAULT_MAX_OUTPUT_BYTES = 200_000
"""每一路输出（stdout / stderr 各算一路）最多保留多少字节。"""

REFUSED_EXIT_CODE = 126
"""被策略拒绝时的退出码。

126 是 shell 约定的「找到了但执行不了」。这里沿用它是为了让
「被挡下」在日志和模型眼里也是一次**执行结果**，而不是一个特殊的错误码。
"""

_CHUNK = 65536
_KILL_GRACE = 5.0
"""杀完进程树后等它真正退出的宽限时间。"""

_DRAIN_GRACE = 5.0
"""进程结束后等读取任务收尾的时间。

进程退出了不代表管道会 EOF：如果它留下了还握着管道的孙进程，EOF 永远不来。
这里不能无限等——那正是超时要解决的问题。
"""

_SPAWN_KWARGS: dict[str, Any] = {"start_new_session": True} if os.name != "nt" else {}
"""POSIX 上给子进程开一个新的会话/进程组。

这不是可有可无的：只有这样 `os.killpg(os.getpgid(pid))` 打到的才是那个
shell 自己一组，否则它和 harness 同组，一杀就杀到自己头上。
Windows 上没有这个概念，用 taskkill /T 走进程树。
"""


class _Sink:
    """一路输出的落点：读到的字节 + 有没有被截断。

    做成对象而不是局部变量，是为了让「读到一半」的数据在读取任务被取消时
    依然留在外面——超时杀树后，往往只能拿到半截输出，那半截仍然有用。
    """

    __slots__ = ("data", "truncated")

    def __init__(self) -> None:
        self.data = bytearray()
        self.truncated = False


async def _pump(stream: asyncio.StreamReader | None, cap: int, sink: _Sink) -> None:
    """把一路输出读进 sink，超过 cap 的字节丢掉。"""
    if stream is None:
        return
    while True:
        chunk = await stream.read(_CHUNK)
        if not chunk:
            return
        room = cap - len(sink.data)
        if room > 0:
            sink.data += chunk[:room]
        if len(chunk) > room:
            sink.truncated = True


def _decode(data: bytearray) -> str:
    """把命令输出解成文本。

    先按 UTF-8 解；解不开就退回系统默认编码（Windows 上 `dir` 这类命令
    输出的是本地代码页，不是 UTF-8）。两条路都走不通才用替换字符——
    丢几个字符，总好过让一次工具调用以 UnicodeDecodeError 收场。
    """
    if not data:
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode(locale.getpreferredencoding(False), errors="replace")


class LocalShell:
    """`ctx.shell` 的本地实现：一条命令、一个超时、两路上限明确的输出。

    `cwd` 默认是工作区根目录，所以模型给的相对路径和 `ctx.fs` 看到的是同一批文件。
    """

    def __init__(
        self,
        root: str | Path,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
        policy: ShellPolicy | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        """建一个 shell。`root` 是命令的工作目录；`policy` 为 None 表示不做命令级检查。"""
        self.cwd = Path(root).resolve()
        self.timeout = float(timeout)
        self.max_output_bytes = int(max_output_bytes)
        self.policy = policy
        self._env = {str(k): str(v) for k, v in (env or {}).items()}
        if self.timeout <= 0:
            raise ValueError("timeout 必须大于 0；不设超时会让一条等输入的命令吊死整个会话")
        if self.max_output_bytes <= 0:
            raise ValueError("max_output_bytes 必须大于 0")

    async def run(
        self,
        command: str,
        *,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
    ) -> ShellResult:
        """跑一条命令，把能预料到的坏事都变成结果。

        `timeout=None` 用实例默认值；显式传一个 <= 0 的值是编程错误，
        会抛 `ToolError`（它经工具管道回到模型那里，模型可以改参数重试）。
        """
        started = time.perf_counter()
        if self.policy is not None:
            refusal = self.policy.check(command)
            if refusal is not None:
                return ShellResult(
                    command=command,
                    exit_code=REFUSED_EXIT_CODE,
                    stderr=f"命令被 shell 策略拒绝：{refusal}",
                    duration=time.perf_counter() - started,
                )

        deadline = self.timeout if timeout is None else float(timeout)
        if deadline <= 0:
            raise ToolError("timeout 必须大于 0；传 None 表示用 shell 插件配置里的默认值")

        merged = dict(os.environ)
        merged.update(self._env)
        if env:
            merged.update({str(k): str(v) for k, v in env.items()})

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=str(self.cwd),
                env=merged,
                stdout=PIPE,
                stderr=PIPE,
                **_SPAWN_KWARGS,
            )
        except OSError as exc:
            return ShellResult(
                command=command,
                exit_code=127,
                stderr=f"起不了进程：{exc}",
                duration=time.perf_counter() - started,
            )

        out_sink, err_sink = _Sink(), _Sink()
        assert proc.stdout is not None and proc.stderr is not None
        out_task = asyncio.create_task(_pump(proc.stdout, self.max_output_bytes, out_sink))
        err_task = asyncio.create_task(_pump(proc.stderr, self.max_output_bytes, err_sink))

        timed_out = False
        try:
            await asyncio.wait_for(proc.wait(), deadline)
        except TimeoutError:
            timed_out = True
            await self._kill_tree(proc)

        await self._settle(out_task)
        await self._settle(err_task)

        stdout = _decode(out_sink.data)
        stderr = _decode(err_sink.data)
        note = f"\n…（输出超过 {self.max_output_bytes} 字节，已截断）…"
        if out_sink.truncated:
            stdout += note
        if err_sink.truncated:
            stderr += note

        return ShellResult(
            command=command,
            exit_code=proc.returncode if proc.returncode is not None else -1,
            stdout=stdout,
            stderr=stderr,
            duration=time.perf_counter() - started,
            timed_out=timed_out,
        )

    # ---------------------------------------------------------------- 内部

    async def _kill_tree(self, proc: asyncio.subprocess.Process) -> None:
        """杀掉整棵进程树，然后等它真的退出。"""
        if proc.returncode is not None:
            return
        if os.name == "nt":
            try:
                killer = await asyncio.create_subprocess_exec(
                    "taskkill", "/F", "/T", "/PID", str(proc.pid),
                    stdout=DEVNULL,
                    stderr=DEVNULL,
                )
                await killer.wait()
            except OSError:
                # 没有 taskkill（精简过的 Windows 镜像）时退回单进程杀：
                # 会漏掉孙进程，但至少不会什么都不做。
                proc.kill()
        else:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except OSError:
                # 进程组拿不到（已经退出、或权限不够）就杀它自己。
                proc.kill()
        # 已经尽力；下面的 _settle 会把读取任务收掉，不会挂住。
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(proc.wait(), _KILL_GRACE)

    @staticmethod
    async def _settle(task: asyncio.Task[None]) -> None:
        """等一路读取任务收尾，最多 `_DRAIN_GRACE` 秒，超时就取消它。"""
        try:
            await asyncio.wait_for(task, _DRAIN_GRACE)
        except TimeoutError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        except asyncio.CancelledError:  # pragma: no cover - 调用方被取消时的顺带情形
            task.cancel()
            raise
