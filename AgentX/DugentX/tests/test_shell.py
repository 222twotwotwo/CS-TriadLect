"""shell 缝的测试 —— 成功、非零退出、超时、被策略拒绝。

超时那条是重点：它不只是断言 `timed_out=True`，还断言**整条测试会在几秒内
结束**。一个「会返回值但是挂住了」的实现，只能靠这条断言抓出来。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from dugentx.kernel.context import Context
from dugentx.kernel.effect import Disposer
from dugentx.kernel.errors import PluginError, ToolError
from dugentx.kernel.loader import resolve_factory
from dugentx.providers.shell_local import LocalShell
from dugentx.seams.shell import ShellPolicy


def py(code: str) -> str:
    """用当前解释器跑一段代码。

    比 `echo` 稳：断言的是 Python 的输出，不是某个 shell 内建命令的行为，
    在 Windows 的 cmd 和 POSIX 的 sh 上结果一致。
    """
    return f'"{sys.executable}" -c "{code}"'


@pytest.fixture
def shell(tmp_path: Path) -> LocalShell:
    return LocalShell(tmp_path, timeout=10.0)


async def test_successful_command(shell: LocalShell) -> None:
    result = await shell.run(py("print('hello-core')"))
    assert result.ok is True
    assert result.exit_code == 0
    assert "hello-core" in result.stdout
    assert result.timed_out is False
    assert result.duration >= 0


async def test_runs_in_the_workspace_root(tmp_path: Path) -> None:
    (tmp_path / "probe.txt").write_text("x", encoding="utf-8")
    shell = LocalShell(tmp_path)
    result = await shell.run(py("import os; print(os.listdir('.'))"))
    assert result.ok is True
    assert "probe.txt" in result.stdout


async def test_non_zero_exit_returns_a_result_not_an_exception(shell: LocalShell) -> None:
    result = await shell.run(py("import sys; sys.exit(3)"))
    assert result.exit_code == 3
    assert result.ok is False
    assert result.timed_out is False


async def test_stderr_is_captured(shell: LocalShell) -> None:
    result = await shell.run(py("import sys; sys.stderr.write('boom')"))
    assert result.ok is True
    assert "boom" in result.stderr
    assert "stderr" in result.render()


async def test_timeout_kills_the_tree_and_returns(tmp_path: Path) -> None:
    shell = LocalShell(tmp_path, timeout=0.5)
    result = await shell.run(py("import time; time.sleep(30)"))
    assert result.timed_out is True
    assert result.ok is False
    assert result.duration < 8, f"超时后没有及时返回：{result.duration:.1f}s"
    assert "超时" in result.render()


async def test_explicit_timeout_overrides_the_default(shell: LocalShell) -> None:
    result = await shell.run(py("import time; time.sleep(30)"), timeout=0.5)
    assert result.timed_out is True


async def test_non_positive_timeout_is_a_programming_error(shell: LocalShell) -> None:
    with pytest.raises(ToolError):
        await shell.run(py("print(1)"), timeout=0)


async def test_policy_denied_command_returns_the_refusal(tmp_path: Path) -> None:
    shell = LocalShell(tmp_path, policy=ShellPolicy())
    result = await shell.run("rm -rf /")
    assert result.ok is False
    assert result.exit_code == 126
    assert "拒绝" in result.stderr
    assert "rm -rf /" in result.stderr  # 说清楚拦的是哪个片段
    assert result.render()  # 仍然是一条能渲染给模型看的结果


async def test_allow_prefixes_narrow_the_surface(tmp_path: Path) -> None:
    shell = LocalShell(tmp_path, policy=ShellPolicy(allow_prefixes=("echo",)))
    refused = await shell.run("ls -la")
    assert refused.ok is False
    assert "白名单" in refused.stderr

    allowed = await shell.run(py("print('ok')"))
    assert allowed.ok is False  # 白名单只放行 echo，python 不算
    echoed = await shell.run("echo ok")
    assert echoed.ok is True
    assert "ok" in echoed.stdout


async def test_output_is_capped_and_says_so(tmp_path: Path) -> None:
    shell = LocalShell(tmp_path, max_output_bytes=1000)
    result = await shell.run(py("print('x'*100000)"))
    assert result.ok is True
    assert len(result.stdout.encode()) < 2000
    assert "已截断" in result.stdout


async def test_missing_working_directory_is_a_result(tmp_path: Path) -> None:
    shell = LocalShell(tmp_path / "nope")
    result = await shell.run(py("print(1)"))
    assert result.ok is False
    assert result.exit_code == 127
    assert "起不了进程" in result.stderr


# ------------------------------------------------------------------ 插件


async def test_shell_plugin_builds_a_local_shell(ctx: Context, tmp_path: Path) -> None:
    plugin = resolve_factory("dugentx.plugins.shell")(
        {"root": str(tmp_path), "timeout": 5, "deny_substrings": ["curl"]}
    )
    dispose: Disposer = await ctx.mount(plugin)

    shell = ctx.service("shell")
    assert isinstance(shell, LocalShell)
    assert shell.cwd == tmp_path.resolve()
    assert shell.timeout == 5.0

    # 配置里的黑名单是**追加**：默认那几条安全网必须还在。
    assert "curl" in shell.policy.deny_substrings
    assert "rm -rf /" in shell.policy.deny_substrings

    assert (await shell.run("curl example.com")).ok is False
    dispose()
    assert ctx.get("shell") is None


async def test_shell_plugin_rejects_bad_limits(ctx: Context, tmp_path: Path) -> None:
    factory = resolve_factory("dugentx.plugins.shell")
    with pytest.raises(PluginError):
        await ctx.mount(factory({"root": str(tmp_path), "timeout": 0}))
