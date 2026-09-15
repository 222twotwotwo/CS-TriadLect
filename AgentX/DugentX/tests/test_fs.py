"""fs 缝的测试 —— 专挑「错了也不会报错」的地方。

路径越界、大小上限、`edit` 的匹配数，这三类问题一旦悄悄放过，模型会在一个
和它以为的完全不同的文件上继续工作，而且没有任何提示。所以它们各有一个测试。
最后还有一个端到端的：把插件真的装起来，从工具注册表里调一遍。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from dugentx.kernel.context import Context
from dugentx.kernel.effect import Disposer
from dugentx.kernel.errors import ToolError
from dugentx.kernel.loader import resolve_factory
from dugentx.providers.fs_local import LocalFileSystem
from dugentx.seams.messages import ToolCall


@pytest.fixture
def fs(tmp_path: Path) -> LocalFileSystem:
    """一个小工作区。写入上限调到 1 KB，测上限不必写一个很长的文件。"""
    return LocalFileSystem(tmp_path, max_bytes=1024)


async def test_write_read_list_edit_round_trip(fs: LocalFileSystem, tmp_path: Path) -> None:
    assert await fs.write_text("notes/a.txt", "第一行\n第二行\n") is True
    assert await fs.read_text("notes/a.txt") == "第一行\n第二行\n"

    entries = await fs.list_dir("notes")
    assert len(entries) == 1
    assert entries[0].path == "notes/a.txt"
    assert entries[0].is_dir is False
    assert entries[0].size == len("第一行\n第二行\n".encode())

    assert await fs.exists("notes/a.txt") is True
    assert fs.absolute("notes/a.txt") == str((tmp_path / "notes" / "a.txt").resolve())

    assert await fs.edit("notes/a.txt", "第二行", "改过的第二行") == 1
    assert await fs.read_text("notes/a.txt") == "第一行\n改过的第二行\n"

    # 同一个路径再写一次是覆盖，不是新建。
    assert await fs.write_text("notes/a.txt", "只剩一行\n") is False


async def test_list_dir_puts_directories_first(fs: LocalFileSystem) -> None:
    await fs.write_text("b.txt", "b")
    await fs.write_text("a/inner.txt", "x")
    await fs.write_text("c.txt", "c")

    entries = await fs.list_dir(".")
    assert [entry.path for entry in entries] == ["a", "b.txt", "c.txt"]
    assert entries[0].is_dir is True
    assert entries[0].render() == "a/"


async def test_write_cap_refuses_and_leaves_nothing_behind(fs: LocalFileSystem) -> None:
    with pytest.raises(ToolError) as err:
        await fs.write_text("big.txt", "x" * 2000)
    assert "上限" in str(err.value)
    assert await fs.exists("big.txt") is False


async def test_read_cap_refuses_oversized_file(tmp_path: Path) -> None:
    fs = LocalFileSystem(tmp_path, max_bytes=100, read_max_bytes=3)
    await fs.write_text("a.txt", "12345")
    with pytest.raises(ToolError) as err:
        await fs.read_text("a.txt")
    assert "读取上限" in str(err.value)


async def test_edit_reports_absent_old_text(fs: LocalFileSystem) -> None:
    await fs.write_text("a.txt", "hello\n")
    with pytest.raises(ToolError) as err:
        await fs.edit("a.txt", "helloo", "x")
    assert "找不到" in str(err.value)
    assert await fs.read_text("a.txt") == "hello\n"


async def test_edit_reports_ambiguous_old_text(fs: LocalFileSystem) -> None:
    await fs.write_text("a.txt", "same\nsame\n")
    with pytest.raises(ToolError) as err:
        await fs.edit("a.txt", "same", "other")
    assert "2 处" in str(err.value)
    assert await fs.read_text("a.txt") == "same\nsame\n"


async def test_edit_replace_all(fs: LocalFileSystem) -> None:
    await fs.write_text("a.txt", "same\nsame\n")
    assert await fs.edit("a.txt", "same", "other", replace_all=True) == 2
    assert await fs.read_text("a.txt") == "other\nother\n"


async def test_relative_escape_is_refused(fs: LocalFileSystem, tmp_path: Path) -> None:
    (tmp_path.parent / "outside.txt").write_text("secret", encoding="utf-8")
    with pytest.raises(ToolError) as err:
        await fs.read_text("../outside.txt")
    assert "工作区之外" in str(err.value)


async def test_absolute_escape_is_refused(fs: LocalFileSystem, tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-abs.txt"
    outside.write_text("secret", encoding="utf-8")

    with pytest.raises(ToolError):
        await fs.read_text(str(outside))
    with pytest.raises(ToolError):
        await fs.write_text(str(outside), "不该写进去")
    with pytest.raises(ToolError):
        # 「不存在」和「不许问」是两件事：越界要报错，不能返回 False。
        await fs.exists(str(outside))
    assert outside.read_text(encoding="utf-8") == "secret"


async def test_extra_roots_are_inside_the_policy(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / "data.txt").write_text("共享内容", encoding="utf-8")
    room = tmp_path / "room"
    room.mkdir()

    fs = LocalFileSystem(room, extra_roots=[shared])
    assert await fs.read_text(str(shared / "data.txt")) == "共享内容"


async def test_non_utf8_file_names_the_file(fs: LocalFileSystem) -> None:
    (fs.root / "bin.dat").write_bytes(b"\xff\xfe\x00\x01")
    with pytest.raises(ToolError) as err:
        await fs.read_text("bin.dat")
    assert "bin.dat" in str(err.value)
    assert "UTF-8" in str(err.value)


async def test_dir_is_not_readable_as_text(fs: LocalFileSystem) -> None:
    await fs.write_text("sub/x.txt", "x")
    with pytest.raises(ToolError) as err:
        await fs.read_text("sub")
    assert "目录" in str(err.value)


# --------------------------------------------------------------- 插件 + 工具


async def _mount(ctx: Context, target: str, config: dict[str, Any]) -> Disposer:
    """走装载器那条路把插件装上：`resolve_factory` 找 `create`，mount 传 config。"""
    plugin = resolve_factory(target)(config)
    return await ctx.mount(plugin)


async def test_tools_end_to_end_over_the_real_plugins(ctx: Context, tmp_path: Path) -> None:
    """把 tools、fs、fs_tools 三个插件装上，从注册表调一遍。

    这条测试同时验三件事：接 ctx 的工具真的拿到了那个上下文、
    `ctx.fs` 就是这一个工作区、工具输出里写清了截断了多少行。
    """
    await _mount(ctx, "dugentx.plugins.tools", {})
    await _mount(ctx, "dugentx.plugins.fs", {"root": str(tmp_path)})
    await _mount(ctx, "dugentx.tools.fs_tools", {})

    registry = ctx.service("tools")
    assert registry.names() == [
        "edit_file",
        "list_dir",
        "read_file",
        "search_text",
        "write_file",
    ]

    async def call(name: str, **arguments: Any) -> str:
        outcome = await registry.execute(
            ToolCall(id="c1", name=name, arguments=arguments)
        )
        assert outcome.ok, outcome.content
        return outcome.content

    assert "已新建 a.txt" in await call("write_file", path="a.txt", content="一\n二\n三\n四\n五\n")
    assert "a.txt" in await call("list_dir")

    clipped = await call("read_file", path="a.txt", max_lines=2)
    assert "共 5 行，显示 1-2" in clipped
    assert "省略了第 3-5 行" in clipped
    assert "start_line=3" in clipped

    assert "已覆盖 a.txt" in await call("write_file", path="a.txt", content="一\n二\n三\n四\n五\n")
    assert "替换 1 处" in await call("edit_file", path="a.txt", old="三", new="叁")

    found = await call("search_text", query="叁")
    assert "a.txt:3" in found
    assert "跳过" in found

    # 越界路径在工具这一层也一样被拒，并且变成一条「执行失败」的结果回给模型，
    # 而不是一个异常把会话打断。
    blocked = await registry.execute(
        ToolCall(id="c2", name="read_file", arguments={"path": "../outside.txt"})
    )
    assert blocked.ok is False
    assert "工作区之外" in blocked.content
