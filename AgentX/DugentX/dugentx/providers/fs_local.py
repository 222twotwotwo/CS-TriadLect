"""本地文件系统 —— `fs` 缝落到真实磁盘上的那一份。

为什么值得单独一层：工具拿到的是**模型给的一串路径文本**，而这串文本可能是
`../../.ssh/id_rsa`。判定「这条路走不走得通」必须只发生一次，且在读写之前，
所以它落在这一层，而不是散在每个工具里各写一遍。路径判定本身交给
`seams.fs.Workspace`（它只做一件事：解析后的真实路径必须落在允许的根里），
这个类负责把「读、写、改、列」翻译成磁盘操作。

两条纪律写在代码里，因为它们各自对应一类很难查的故障：

- **写之前先量。** 超过上限就整个拒绝，并且说清楚为什么。半个大文件比一次
  失败的写入难查得多。
- **读进来必须是 UTF-8。** 解不开就报错并点名文件。给模型一串替换字符，
  它看不出那和真内容的区别，于是会在假的文本上继续推理。

磁盘操作都走 `asyncio.to_thread`：一个 `async def` 里同步读 200 MB 文件，
这个 `async` 就只是装饰——它会把整个事件循环连同别的任务一起冻住。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from dugentx.kernel.errors import ToolError
from dugentx.seams.fs import FileEntry, Workspace

DEFAULT_MAX_BYTES = 1_000_000
"""单次写入的上限（UTF-8 字节数），1 MB 左右。

这是护栏不是配额：模型一次写出超过这个量的内容，多数时候不是它想说这么多，
而是它把整份文件重打了一遍——那正是 `edit` 该出场的地方。
"""

DEFAULT_READ_MAX_BYTES = 2_000_000
"""单次读取的上限（UTF-8 字节数）。

读没有上限的话，一次 `read_file` 就能把上下文和内存一起吃掉；
而超过这个体积的文件，模型本来也不可能完整看进上下文。
"""


class LocalFileSystem:
    """`ctx.fs` 的本地实现：所有路径相对工作区根解析，越界当场拒绝。

    换一个实现（只读快照、远程沙箱）不需要改任何调用方——它们只认
    `ctx.fs` 的四个方法。这个类是那个默认实现。
    """

    def __init__(
        self,
        root: str | Path,
        *,
        extra_roots: list[str | Path] | None = None,
        max_bytes: int = DEFAULT_MAX_BYTES,
        read_max_bytes: int = DEFAULT_READ_MAX_BYTES,
    ) -> None:
        """建一个工作区。`root` 是唯一必需参数，其余都有边界明确的上限。"""
        self.workspace = Workspace(root, extra_roots=extra_roots)
        self.root: Path = self.workspace.root
        self.max_bytes = int(max_bytes)
        self.read_max_bytes = int(read_max_bytes)
        if self.max_bytes <= 0 or self.read_max_bytes <= 0:
            # 0 或者负数当上限是配错了，不是「无上限」。大声失败。
            raise ValueError("max_bytes 和 read_max_bytes 必须大于 0")

    # ------------------------------------------------------------------ 读

    async def read_text(self, path: str) -> str:
        """把一个 UTF-8 文本文件读成字符串。

        读取走 Python 的通用换行：文件里的 `\\r\\n` 读进来是 `\\n`。
        这条和写入的「不转换」合起来有一个后果，是**有意**选的：
        在 Windows 上改一个 CRLF 文件，它的换行会被统一成 LF。
        反过来（读时保留 `\\r`、写时按平台转换）会让模型从 `read_file`
        抄下来的 `old` 永远匹配不上，而那种失败看起来像「文件里没有这段文字」——
        比换行风格的变化难查得多。
        """
        target = self.workspace.resolve(path, must_exist=True)
        return await asyncio.to_thread(self._read_sync, target, path)

    def _read_sync(self, target: Path, path: str) -> str:
        if target.is_dir():
            raise ToolError(f"{path} 是目录，不是文件；要看里面有什么请用 list_dir")
        size = self._size_of(target, path)
        if size > self.read_max_bytes:
            raise ToolError(
                f"{path} 有 {size} 字节，超过单次读取上限 {self.read_max_bytes}；"
                f"请用 shell 工具分段取（例如 head / sed -n），或者换一个更小的文件"
            )
        try:
            return target.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ToolError(
                f"{path} 不是 UTF-8 文本（第 {exc.start} 字节处解不开）；"
                f"二进制文件请用 shell 工具处理"
            ) from exc
        except OSError as exc:
            raise ToolError(f"读不了 {path}：{exc}") from exc

    # ------------------------------------------------------------------ 写

    async def write_text(self, path: str, content: str) -> bool:
        """整份写入一个文件，父目录不存在就一并建出来。

        返回「这个文件是不是刚刚被新建的」。`FileSystem` 缝把返回值标成了
        `None`，所以这是**扩展**而不是违约：只调它、不看返回值的调用方照旧工作，
        而工具层需要这个 bool——「新建」和「覆盖」对模型是两件后果不同的事，
        后者会丢掉原有的内容。
        """
        target = self.workspace.resolve(path)
        self._guard_write(path, content)
        return await asyncio.to_thread(self._write_sync, target, path, content)

    @staticmethod
    def _write_sync(target: Path, path: str, content: str) -> bool:
        if target.is_dir():
            raise ToolError(f"{path} 是目录，写不进去")
        created = not target.exists()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            # newline="" —— 不做换行转换：模型给什么字节，磁盘上就是什么字节。
            # 默认值会在 Windows 上把每个 \n 偷偷换成 \r\n，于是「写 20 字节」
            # 变成 22 字节，而且同一份工作在两个平台上产出不同的文件。
            target.write_text(content, encoding="utf-8", newline="")
        except OSError as exc:
            raise ToolError(f"写不了 {path}：{exc}") from exc
        return created

    def _guard_write(self, path: str, content: str) -> None:
        size = len(content.encode("utf-8"))
        if size > self.max_bytes:
            raise ToolError(
                f"{path} 的内容有 {size} 字节，超过写入上限 {self.max_bytes}；"
                f"请拆成多次小改动（`edit_file` 只写改动的那一段），"
                f"或者调大 fs 插件的 max_bytes"
            )

    # ------------------------------------------------------------------ 改

    async def edit(self, path: str, old: str, new: str, *, replace_all: bool = False) -> int:
        """把文件里的一段文本换成另一段，返回替换了几处。

        匹配数**必须**是 1，除非显式 `replace_all`。0 处要报错是因为：
        静默的成功返回会让模型相信它已经改好了，然后接着在旧代码上推理——
        这是这套工具能犯的最坏的错误。多于 1 处也报错，因为「换了哪一处」
        不该由实现替模型猜。两条信息的重点都是「怎么让它唯一」，不是「失败了」。
        """
        if not old:
            raise ToolError("要替换的旧文本不能是空串；空串在文件里处处都匹配")
        target = self.workspace.resolve(path, must_exist=True)
        content = await asyncio.to_thread(self._read_sync, target, path)
        count = content.count(old)
        if count == 0:
            raise ToolError(
                f"{path} 里找不到要替换的旧文本（一个字都不能差，包括缩进和换行）；"
                f"先用 read_file 看一眼实际内容，再多带一行上下文"
            )
        if count > 1 and not replace_all:
            raise ToolError(
                f"{path} 里有 {count} 处匹配，无法确定改哪一处；"
                f"把上下文写长一点让它唯一，或者传 replace_all=true 一次全换"
            )
        updated = content.replace(old, new)
        self._guard_write(path, updated)
        await asyncio.to_thread(self._write_sync, target, path, updated)
        return count

    # ------------------------------------------------------------------ 看

    async def list_dir(self, path: str = ".") -> list[FileEntry]:
        """列一个目录，目录在前、同类按名字排序。"""
        target = self.workspace.resolve(path, must_exist=True)
        return await asyncio.to_thread(self._list_sync, target, path)

    def _list_sync(self, target: Path, path: str) -> list[FileEntry]:
        if not target.is_dir():
            raise ToolError(f"{path} 不是目录；要看文件内容请用 read_file")
        try:
            # 一次 stat 拿到类型再排序：排序键里再调 is_dir() 会多走一遍系统调用。
            items = [(child, child.is_dir()) for child in target.iterdir()]
        except OSError as exc:
            raise ToolError(f"列不了 {path}：{exc}") from exc
        items.sort(key=lambda item: (not item[1], item[0].name.lower()))

        entries: list[FileEntry] = []
        for child, is_dir in items:
            size = 0
            if not is_dir:
                try:
                    size = child.stat().st_size
                except OSError:
                    size = 0  # 断掉的符号链接之类：大小未知，仍然列出来
            entries.append(
                FileEntry(path=self.workspace.relative(child), is_dir=is_dir, size=size)
            )
        return entries

    async def exists(self, path: str) -> bool:
        """路径存不存在。

        工作区之外的路径会**报错**而不是返回 False：「不存在」和「不许问」
        是两件不同的事，混成一个 False 会让一次越界访问看起来像一次正常查询。
        """
        target = self.workspace.resolve(path)
        return await asyncio.to_thread(target.exists)

    def absolute(self, path: str) -> str:
        """把工作区相对路径解析成绝对路径（只解析，不碰磁盘）。"""
        return str(self.workspace.resolve(path))

    # ---------------------------------------------------------------- 内部

    @staticmethod
    def _size_of(target: Path, path: str) -> int:
        try:
            return target.stat().st_size
        except OSError as exc:
            raise ToolError(f"看不到 {path} 的大小：{exc}") from exc
