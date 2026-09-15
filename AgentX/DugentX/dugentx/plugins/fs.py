"""fs 插件 —— 提供本地文件系统（`ctx.fs`）。

它只做一件事：把配置翻译成一个 `LocalFileSystem`，然后挂到 `fs` 这个键上。
工具（`dugentx/tools/fs_tools.py`）不认识这个类，只认 `ctx.fs`；
换成只读快照或远程沙箱时，改的是配置里的那一行 `plugin`，不是工具。

配置：

- `root`（必填）：工作区根目录。工具给的路径相对它解析，越界的当场拒绝。
  相对路径按进程当前目录解析——写绝对路径可以少一个变量。
- `extra_roots`：额外允许的根，比如一个共享的资料目录。
- `max_bytes`：单次写入上限（默认 1 MB）。
- `read_max_bytes`：单次读取上限（默认 2 MB）。
"""

from __future__ import annotations

from typing import Any

from dugentx.kernel.context import Context
from dugentx.kernel.errors import PluginError
from dugentx.kernel.plugin import define_plugin
from dugentx.providers.fs_local import (
    DEFAULT_MAX_BYTES,
    DEFAULT_READ_MAX_BYTES,
    LocalFileSystem,
)


@define_plugin(
    "fs",
    provides=("fs",),
    description="本地文件系统，所有路径都要落在工作区根里面",
)
def create(ctx: Context, config: dict[str, Any]) -> None:
    """装载本地文件系统。缺 `root` 就拒绝装载。

    这里不在缺配置时猜一个默认根（比如当前目录）：根目录决定了模型能碰哪些
    文件，猜错的后果是它开始改一个和它以为的完全不同的项目。宁可装载失败。
    """
    root = config.get("root")
    if not root:
        raise PluginError(
            "fs 插件需要 config.root（工作区根目录）；"
            "没配的话路径策略没有基准，宁可装载失败也不猜一个"
        )

    try:
        max_bytes = int(config.get("max_bytes", DEFAULT_MAX_BYTES))
        read_max_bytes = int(config.get("read_max_bytes", DEFAULT_READ_MAX_BYTES))
    except (TypeError, ValueError) as exc:
        raise PluginError(f"fs 的 max_bytes / read_max_bytes 必须是整数：{exc}") from exc

    try:
        filesystem = LocalFileSystem(
            root,
            extra_roots=list(config.get("extra_roots") or []),
            max_bytes=max_bytes,
            read_max_bytes=read_max_bytes,
        )
    except (ValueError, OSError) as exc:
        raise PluginError(f"fs 的配置有问题：{exc}") from exc

    ctx.provide("fs", filesystem)
