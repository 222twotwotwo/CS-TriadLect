"""文件工具 —— 模型读写工作区的那一组。

每个工具都只是 `ctx.fs` 的一层薄翻译：路径策略、大小上限、编码检查全在
fs 缝里，这里负责把结果变成**模型读得懂的一段话**。这句话值得说清楚：
工具的输出是模型下一步推理的输入，所以「一共多少行、显示的是哪一段、
省略了多少」这些字必须写出来。少写一个字，模型就会以为自己看到了全文，
然后在没看到的那部分上继续推理。

标签是这个模块唯一的策略表达：读的是 `read`（自动放行），
写和改的是 `write`（要问一句）。工具名不参与分级——新加一个写文件的工具，
只要打上 `write`，权限策略自动管住它。

五个工具装在同一个模块里，因为它们共享同一份「怎么给模型看」的措辞；
拆开的好处只有一个，就是让每个文件更短，那不值得。
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from typing import Any

from dugentx.kernel.context import Context
from dugentx.kernel.effect import Disposer
from dugentx.kernel.errors import ToolError
from dugentx.kernel.plugin import define_plugin
from dugentx.seams.tools import LABEL_READ, LABEL_WRITE, tool_from_function

DEFAULT_LINES = 200
"""`read_file` 默认读多少行。够看清一个函数，又不会一次吃掉小半个上下文。"""

MAX_LIST_ENTRIES = 200
"""`list_dir` 最多列多少项。一个大目录列全了只会把有用的一行冲走。"""

MAX_LINE_CHARS = 500
"""`read_file` 里单行最多显示多少字符。

压缩过的 js / 一行 json 能到几十万字符，一行就够吃掉整个上下文。
"""

MAX_MATCH_LINE_CHARS = 200
"""搜索命中的那一行最多显示多少字符。命中行只是线索，不是正文。"""

DEFAULT_IGNORES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        ".venv",
        "venv",
        "node_modules",
        "dist",
        "build",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".idea",
        ".vscode",
    }
)
"""搜索时默认跳过的目录名。

列表按名字匹配，不按路径：`**/node_modules/**` 这种写法要求模型先理解
glob 和路径层级，而它此刻只想找一段字符串。要搜这些目录里的东西，
直接给出 `path` 指到里面即可。
"""

SEARCH_MAX_FILES = 2000
"""一次搜索最多真正读多少个文件。"""

SEARCH_MAX_FILE_BYTES = 1_000_000
"""搜索时单个文件的大小上限；超了按「跳过」计数，不读。"""


def _clip(line: str, limit: int) -> str:
    """截断一行，并说明还有多少字符没显示。"""
    if len(line) <= limit:
        return line
    return f"{line[:limit]}…（本行还有 {len(line) - limit} 个字符未显示）"


async def read_file(
    ctx: Context,
    path: str,
    start_line: int = 1,
    max_lines: int = DEFAULT_LINES,
) -> str:
    """读取工作区里的一个文本文件，带行号返回。

    行号是给你的：你可以直接说「第 42 行」而不必再数一遍。
    每次读都会说明总行数和实际显示的区间，读到一半时会告诉你还有多少行没显示——
    不要在看到「省略」之后假装自己看到了全文。

    Args:
        path: 相对工作区根的路径，例如 dugentx/cli.py
        start_line: 从第几行开始读，从 1 数起
        max_lines: 最多读多少行
    """
    if start_line < 1:
        raise ToolError("start_line 从 1 数起；要读开头就传 1")
    if max_lines < 1:
        raise ToolError("max_lines 至少要 1")

    text = await ctx.service("fs").read_text(path)
    lines = text.splitlines()
    total = len(lines)
    if total == 0:
        return f"{path}（空文件，0 行）"
    if start_line > total:
        raise ToolError(f"{path} 只有 {total} 行，从第 {start_line} 行开始什么都没读到")

    chunk = lines[start_line - 1 : start_line - 1 + max_lines]
    first = start_line
    last = start_line + len(chunk) - 1
    width = len(str(last))
    body = "\n".join(
        f"{number:>{width}}\t{_clip(line, MAX_LINE_CHARS)}"
        for number, line in enumerate(chunk, start=first)
    )

    header = f"{path}（共 {total} 行，显示 {first}-{last}）"
    omitted: list[str] = []
    if first > 1:
        omitted.append(f"前面省略了第 1-{first - 1} 行")
    if last < total:
        omitted.append(
            f"后面省略了第 {last + 1}-{total} 行，共 {total - last} 行；"
            f"要接着读就传 start_line={last + 1}"
        )
    if not omitted:
        return f"{header}\n{body}"
    return f"{header}\n{body}\n…（{'；'.join(omitted)}）…"


async def write_file(ctx: Context, path: str, content: str) -> str:
    """把一份内容整个写进一个文件（没有就新建，有就**整份覆盖**）。

    覆盖会丢掉原文件里的一切。只改一小段时用 `edit_file`：它按上下文替换，
    不会顺手删掉你没看过的那部分。父目录不存在时会自动建出来。

    Args:
        path: 相对工作区根的路径
        content: 要写入的完整文件内容
    """
    created = await ctx.service("fs").write_text(path, content)
    size = len(content.encode("utf-8"))
    how = "新建" if created else "覆盖"
    return f"已{how} {path}（{size} 字节）"


async def edit_file(ctx: Context, path: str, old: str, new: str, replace_all: bool = False) -> str:
    """把一个文件里的一段文本换成另一段，文件其余部分不动。

    要替换的 `old` 必须在文件里恰好出现一次，否则这次调用会被拒绝并告诉你
    出现了几次。这不是苛刻：如果它匹配到多处而实现替你挑了一处，你会以为
    改的是另一处，然后在这之上继续工作。给的上下文长一点，让它唯一。

    Args:
        path: 相对工作区根的路径
        old: 要被替换掉的原文，必须与文件里的内容逐字符一致（含缩进）
        new: 替换成的新文本
        replace_all: 确实想一次全换时传 true；默认只允许唯一匹配
    """
    count = await ctx.service("fs").edit(path, old, new, replace_all=replace_all)
    return f"已在 {path} 里替换 {count} 处"


async def list_dir(ctx: Context, path: str = ".") -> str:
    """列出一个目录里的文件和子目录，目录排在前面。

    每一项都会标出是目录还是文件；文件带字节数。要读哪个文件，
    把这里给出的路径直接传给 `read_file`。

    Args:
        path: 相对工作区根的目录路径，默认是工作区根
    """
    entries = await ctx.service("fs").list_dir(path)
    if not entries:
        return f"{path}（空目录）"
    shown = entries[:MAX_LIST_ENTRIES]
    lines = [f"{path}/（{len(entries)} 项）"]
    lines.extend(entry.render() for entry in shown)
    if len(entries) > len(shown):
        lines.append(f"…（还有 {len(entries) - len(shown)} 项未显示）…")
    return "\n".join(lines)


async def search_text(ctx: Context, query: str, path: str = ".", max_matches: int = 50) -> str:
    """在工作区里递归搜索一段文本，返回命中的文件与行号。

    按子串匹配，不是正则——你想找的通常是「那段代码在哪」，而正则里的
    括号和点号会让你搜不到本来存在的东西。大小写不敏感。
    结果里带文件路径和行号，可以直接接着用 `read_file` 看上下文。

    Args:
        query: 要找的文本片段（大小写不敏感）
        path: 从哪个目录开始往下搜，默认是工作区根
        max_matches: 最多返回多少处命中
    """
    if not query:
        raise ToolError("要搜的文本不能是空串；空串在每一行都命中")
    if max_matches < 1:
        raise ToolError("max_matches 至少要 1")

    files = ctx.service("fs")
    needle = query.lower()
    targets: deque[str] = deque([path])
    matches: list[str] = []
    scanned = 0
    ignored = 0
    too_big = 0
    unreadable = 0
    file_limit_hit = False

    while targets and len(matches) < max_matches and not file_limit_hit:
        for entry in await files.list_dir(targets.popleft()):
            if entry.path.rsplit("/", 1)[-1] in DEFAULT_IGNORES:
                ignored += 1
                continue
            if entry.is_dir:
                targets.append(entry.path)
                continue
            if scanned >= SEARCH_MAX_FILES:
                file_limit_hit = True
                break
            if entry.size > SEARCH_MAX_FILE_BYTES:
                too_big += 1
                continue
            try:
                text = await files.read_text(entry.path)
            except ToolError:
                # 读不了的文件（二进制、权限）算「跳过」，不算这次搜索失败：
                # 一次搜索里有几个读不了的文件是常态，报错会让模型放弃整次搜索。
                unreadable += 1
                continue
            scanned += 1
            for number, line in enumerate(text.splitlines(), start=1):
                if needle in line.lower():
                    matches.append(f"{entry.path}:{number}: {_clip(line, MAX_MATCH_LINE_CHARS)}")
                    if len(matches) >= max_matches:
                        break

    if matches:
        head = f"搜索 {query!r}：命中 {len(matches)} 处（扫了 {scanned} 个文件）"
    else:
        head = f"搜索 {query!r}：在 {scanned} 个文件里没有命中"

    lines = [head, *matches]
    if len(matches) >= max_matches:
        lines.append(f"…（只显示前 {max_matches} 处；缩小 path 或加长 query 可以看得更准）…")
    if file_limit_hit:
        lines.append(f"…（扫到 {SEARCH_MAX_FILES} 个文件就停了，还有目录没看完）…")
    lines.append(
        f"（跳过 {ignored + too_big + unreadable} 个文件：忽略名单 {ignored}、"
        f"超过 {SEARCH_MAX_FILE_BYTES} 字节 {too_big}、读不了 {unreadable}）"
    )
    return "\n".join(lines)


_TOOLS: tuple[tuple[Callable[..., Any], frozenset[str]], ...] = (
    (read_file, frozenset({LABEL_READ})),
    (list_dir, frozenset({LABEL_READ})),
    (search_text, frozenset({LABEL_READ})),
    (write_file, frozenset({LABEL_WRITE})),
    (edit_file, frozenset({LABEL_WRITE})),
)
"""工具和它们的标签。

顺序就是给模型看 schema 的顺序，读的在前写的在后——工具列表本身也是提示词，
把只读的放在前面，模型在犹豫时更可能先去读一眼。
"""


def register(ctx: Context) -> Disposer:
    """注册全部文件工具，返回一个能一次性撤掉它们的 disposer。

    处理函数第一个参数是 `ctx`，注册表按名字把它注进去，所以这里不需要
    任何包装：`tool_from_function` 只看签名里的业务参数，模型也只看得到那些。
    """
    registry = ctx.service("tools")
    disposers = [
        registry.register(tool_from_function(fn, labels=labels)) for fn, labels in _TOOLS
    ]

    def dispose() -> None:
        # 反序撤销：先撤最后注册的那个，和建立顺序对称。
        for dispose_one in reversed(disposers):
            dispose_one()

    return dispose


@define_plugin(
    "fs_tools",
    inject=("tools", "fs"),
    description="文件工具：读、列、搜、写、改",
)
def create(ctx: Context, config: dict[str, Any]) -> None:
    """装载文件工具。它不需要配置——上限和忽略名单在 fs 与工具自身里。

    `inject` 里的 `fs` 不只是为了让装载器排序，也是这里真正的依赖：
    注册工具时不做检查，真正的失败会发生在第一次调用上；声明依赖让这张图
    在装载时就是完整的。
    """
    del config  # 没有可配的东西；参数保留是为了和别的插件保持同一个签名
    ctx.effect(register, label="tools:fs")
