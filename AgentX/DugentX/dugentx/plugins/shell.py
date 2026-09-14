"""shell 插件 —— 提供本地命令执行（`ctx.shell`）。

配置：

- `root`：命令的工作目录，默认是进程当前目录。通常配成和 fs 的 `root` 一样，
  这样模型写 `python -m pytest tests` 时，命令看到的文件和工具看到的是同一批。
- `timeout`：默认超时（秒，默认 60）。
- `max_output_bytes`：每一路输出保留的上限（默认 200000）。
- `allow_prefixes`：白名单前缀。**配了就只放行这些前缀**，这是收窄面的开关。
- `deny_substrings`：追加到默认黑名单上的片段。
- `env`：附加到子进程环境变量上的一组键值。

关于黑名单为什么是「追加」：`ShellPolicy` 自带的默认片段（`rm -rf /`、`mkfs`、
fork 炸弹之类）是安全网，配置只能往上加，不能因为某一行配置漏写了某个键
就把它整个关掉。想彻底关掉，那是改代码的事，不是改配置的事。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dugentx.kernel.context import Context
from dugentx.kernel.errors import PluginError
from dugentx.kernel.plugin import define_plugin
from dugentx.providers.shell_local import (
    DEFAULT_MAX_OUTPUT_BYTES,
    DEFAULT_TIMEOUT,
    LocalShell,
)
from dugentx.seams.shell import ShellPolicy


@define_plugin(
    "shell",
    provides=("shell",),
    description="本地命令执行：带超时、整棵进程树击杀、输出上限和命令黑白名单",
)
def create(ctx: Context, config: dict[str, Any]) -> None:
    """装载本地 shell。"""
    root = config.get("root") or Path.cwd()

    try:
        timeout = float(config.get("timeout", DEFAULT_TIMEOUT))
        max_output_bytes = int(config.get("max_output_bytes", DEFAULT_MAX_OUTPUT_BYTES))
    except (TypeError, ValueError) as exc:
        raise PluginError(f"shell 的 timeout / max_output_bytes 类型不对：{exc}") from exc

    policy = ShellPolicy()
    policy.allow_prefixes = tuple(str(p) for p in (config.get("allow_prefixes") or ()))
    policy.deny_substrings = (
        *policy.deny_substrings,
        *(str(s) for s in (config.get("deny_substrings") or ())),
    )

    try:
        shell = LocalShell(
            root,
            timeout=timeout,
            max_output_bytes=max_output_bytes,
            policy=policy,
            env={str(k): str(v) for k, v in (config.get("env") or {}).items()},
        )
    except (ValueError, OSError) as exc:
        raise PluginError(f"shell 的配置有问题：{exc}") from exc

    ctx.provide("shell", shell)
