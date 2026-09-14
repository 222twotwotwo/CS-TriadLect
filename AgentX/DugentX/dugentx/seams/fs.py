"""fs 缝 —— 文件访问。

把它做成缝而不是直接 `open()`，是为了让「执行世界」可以整体搬走：
本地目录、只读快照、远程沙箱，背后是同一个接口。dsh 里 fs 和 subprocess
共享一个执行世界，所以把两者指向远端沙箱，Bash、PTY、LSP 会一起跟过去。

`Workspace` 还负责**路径策略**：工具拿到的路径是模型给的，可能是
`../../etc/passwd`，也可能是绝对路径。判定必须发生在这一层，
而不是在每个工具里各写一遍。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from dugentx.kernel.errors import ToolError


@dataclass(slots=True)
class FileEntry:
    path: str
    is_dir: bool
    size: int = 0

    def render(self) -> str:
        return f"{self.path}/" if self.is_dir else f"{self.path}  ({self.size}B)"


@runtime_checkable
class FileSystem(Protocol):
    """`ctx.fs`。所有方法都按工作区相对路径工作。"""

    root: Path

    async def read_text(self, path: str) -> str: ...

    async def write_text(self, path: str, content: str) -> None: ...

    async def edit(self, path: str, old: str, new: str, *, replace_all: bool = False) -> int:
        """返回替换了几处。0 处就是要报错的信号——模型以为改了，其实没改。"""
        ...

    async def list_dir(self, path: str = ".") -> list[FileEntry]: ...

    async def exists(self, path: str) -> bool: ...

    def absolute(self, path: str) -> str: ...


class Workspace:
    """路径策略的唯一判定点。

    规则很短：解析后的真实路径必须落在允许的根里面。
    用 `resolve()` 而不是字符串前缀比较——`a/../../b` 这种写法
    只有真的解析过才知道它去了哪。
    """

    def __init__(self, root: str | Path, *, extra_roots: list[str | Path] | None = None) -> None:
        self.root = Path(root).resolve()
        self.roots = [self.root, *(Path(p).resolve() for p in (extra_roots or []))]

    def resolve(self, path: str, *, must_exist: bool = False) -> Path:
        candidate = (
            (self.root / path).resolve()
            if not Path(path).is_absolute()
            else Path(path).resolve()
        )
        if not any(candidate == r or r in candidate.parents for r in self.roots):
            raise ToolError(
                f"路径 {path!r} 落在工作区之外（工作区：{self.root}）；"
                f"这道限制在 fs 缝里，工具改不了"
            )
        if must_exist and not candidate.exists():
            raise ToolError(f"没有这个路径：{path}")
        return candidate

    def relative(self, path: Path) -> str:
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return str(path)
