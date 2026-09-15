"""formatting —— 把 agent 正在发生的事变成人看得懂的文字。

这一层不认识 textual，也不认识终端，只回答一个问题：**这件事该长成哪几行？**
答案写成纯函数，于是两条路——交互界面（`app.py`）与非交互输出（`--once`、CI、
`examples/tui_demo.py`）——用的是同一份排版。这不是为了省代码，而是为了不让它们
分叉：界面上把 500 行输出折叠成 10 行、而管道里全画出来的话，「你看到的」和
「你脚本里拿到的」就成了两件不同的事。

几条不肯让步的约定：

- **流式文本不提前换行。** 模型按 token 吐字，每块后面都换行会得到一屏碎句，
  所以 delta 只是追加，`assistant_end` 才收尾。
- **长东西折叠，并且说出折了多少。** 一次 `run_command` 可能吐 500 行，全画出来
  会把有用的几行冲走；只画开头不说「省略了多少」又会让人以为看到了全部。两个都
  不行，所以：截断，并且写明数量。
- **不用 emoji，但用字形说话。** 上色是界面的事（`classify()` 把一行归到一种
  语气，怎么上色由界面决定），而**字形是排版**：它在纯文本形态下照样在，
  于是「这一行是什么」在管道里、在日志里也读得出来。全部字形就下面这几个，
  多一个都要先说清楚它比现有的好在哪：

  | 字形 | 意思 | 谁画的 |
  |---|---|---|
  | `•` | 人说的话 | `USER_MARKER` |
  | `▸` | 助手的正文 | `ASSISTANT_MARKER` |
  | `◦` | 人对提问的回答 | `ANSWER_MARKER` |
  | `?` | 一次提问 | `question_lines` |
  | `╭─` `│` | 工具卡的顶边 / 正文边 | `tool_call_line` / `tool_result_lines` |
  | `✓` `✗` `⊘` | 成功 / 失败 / 被拦下（没执行） | `result_glyph` |
  | `!` | 警告 | `notice_line` |
  | `──` | 一步、一个回合的边界 | `step_line` / `turn_end_line` |
  | `·` | 低声旁白；状态条空闲 | 各处 dim 旁白 / `STATUS_IDLE` |
  | `⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏` | 正在跑（只在状态条上转） | `spinner_frame` |

  这套字形和 `dugentx/cli.py` 里那份事件回显是同一套（`· step`、`✓/⊘/✗`、`?`）：
  它们是同一件事在两个地方，两套字形只会让人以为看的是两回事。
- **不转圈。** 动画只属于界面：`--once` 的输出会进管道、进日志、进 CI 的记录，
  里面跳动的帧不携带任何信息，只是把日志弄脏。字形是排版，动画不是。
"""

from __future__ import annotations

import difflib
import json
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from typing import IO, TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from dugentx.seams.agent import TurnResult
    from dugentx.seams.human import Question

USER_MARKER = "• "
ASSISTANT_MARKER = "▸ "
ANSWER_MARKER = "◦ "
"""三种行的前缀。它们不是装饰，是「这句话是谁说的」在**没有颜色**时的唯一线索，
也是界面决定怎么上色的依据（见 `classify`）。"""

CARD_TOP = "╭─"
CARD_BODY = "│"
"""工具卡的边。一行 `╭─` 开一张卡，下面每一行都以 `│` 起头——所以「这几行属于
同一次工具调用」在屏幕上是看得出来的，不用人去数缩进。

**没有底边。** 一张卡结束的信号就是下一条 `╭─`；再加一行只有符号的 `╰─`，
在一屏几十行的流水账里只是噪声。"""

OK_GLYPH = "✓"
FAILED_GLYPH = "✗"
BLOCKED_GLYPH = "⊘"
"""结果的三个字形。被拦下单独一档，因为它不是失败：工具根本没跑，是策略说不跑。
这两种在屏幕上必须能分开，人看到才知道该去改代码还是改策略。"""

WARN_GLYPH = "!"
ASK_GLYPH = "?"
RULE = "──"
NOTE_GLYPH = "·"
"""警告、提问、分节、旁白。`·` 出现在好几处，含义只有一个：这一行是低声的，
它不要求你做任何事。"""

STATUS_IDLE = "·"
"""状态条空闲时的字形。和旁白用的是同一个 `·`：它在这两处是一个意思——低调。

它**不进 `status_line`**：`/status` 印的是事实本身，不该带一个「此刻在不在跑」
的状态字（那份输出也会进管道和日志，而「空闲」在日志里没有意义）。"""

SPINNER_FRAMES: tuple[str, ...] = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
"""转一圈的十帧（Braille）。十个够看出在动，又不至于晃眼。"""

SPINNER_STATIC = "⠿"
"""不转的时候那一帧：填满的点阵，看着就是「停着的」——而不是「转到了某一帧」。"""

SPINNER_INTERVAL = 0.08
"""两帧之间多少秒。pi 用的是 80 毫秒；跟着它，「在动」这件事读起来才一样。"""

WRITE_TOOLS: frozenset[str] = frozenset({"write_file", "edit_file"})
"""调用这些工具时，父层应该在结果之后补一段 diff。

这里点名写死，而不是按 `LABEL_WRITE` 标签推论：标签说的是「这次调用要问一句人」，
不是「它改了一个文件」。`run_command`、`delegate_task` 也带 write 标签，
但它们没有可对比的前后文本——给它们画 diff 只会画出一段空话。
名单必须和 `dugentx/tools/fs_tools.py` 里真正注册的名字一致，所以测试是拿
那张注册表算出来的，不是把这两个名字抄第二遍。
"""

RESULT_HEAD_LINES = 6
RESULT_TAIL_LINES = 4
"""工具输出折叠后保留的头尾行数。

两头都留是有原因的：开头通常写着这次调用的结论，结尾通常写着「省略 / 失败 /
下一步怎么办」。只留头会漏掉最后那句提示，只留尾会不知道这次到底干了什么。
"""

RESULT_LINE_CHARS = 200
"""单行最多画多少字符。500 行输出里最长的那行往往比整段还长。"""

_PRIMARY_ARG: dict[str, str] = {
    "read_file": "path",
    "write_file": "path",
    "edit_file": "path",
    "list_dir": "path",
    "search_text": "query",
    "run_command": "command",
}
"""每个工具最该先被看到的那一个参数。摘要的行首留给它。"""

_FALLBACK_PRIMARY: tuple[str, ...] = ("path", "command", "query", "name", "prompt", "task")
"""不认识的工具按这个顺序找「最像要点」的参数；都没有就取第一个。"""

_BODY_ARGS = frozenset({"content", "old", "new", "body", "text", "patch"})
"""正文类参数：它们的值是整份文件。摘要里只配露一个开头。"""

_HEAD_CHARS = 120
_ARG_CHARS = 60
_BODY_CHARS = 40
"""三种截断上限。正文类掐得最狠，因为它的长度和「这次要看什么」无关。"""

_NO_NEWLINE = "\\ No newline at end of file"
"""结尾没有换行时的标记行，照 diff(1) 的写法。"""


# ---------------------------------------------------------------------- 摘要


def summarize_arguments(name: str, arguments: dict[str, Any]) -> str:
    """一行摘要：这个工具这次真正要看的那几个参数。

    顺序不是字母序，是「谁最要紧」：先给路径或命令，其余按原顺序跟在后面。
    正文类参数（`content` / `old` / `new`）只露开头一个角——一次 `write_file`
    能把 500 行塞进 `content`，原样画出来就是一屏废话，而真正要看的
    「写哪个文件」会被冲走。

    值被折过就一定说出来折了多少：一句被悄悄截断的参数，读起来和完整的一样真。
    """
    if not arguments:
        return ""
    primary = _primary_key(name, arguments)
    parts = [_render_value(primary, arguments[primary], is_primary=True)]
    for key, value in arguments.items():
        if key == primary or value is None or value == "":
            continue
        parts.append(f"{key}={_render_value(key, value)}")
    return "  ".join(parts)


def _primary_key(name: str, arguments: dict[str, Any]) -> str:
    known = _PRIMARY_ARG.get(name)
    if known is not None and known in arguments:
        return known
    for candidate in _FALLBACK_PRIMARY:
        if candidate in arguments:
            return candidate
    return next(iter(arguments))


def _render_value(key: str, value: Any, *, is_primary: bool = False) -> str:
    """把一个参数值压成一行。非字符串的用 JSON 表示，不猜它的形状。"""
    limit = _HEAD_CHARS if is_primary else (_BODY_CHARS if key in _BODY_ARGS else _ARG_CHARS)
    if isinstance(value, str):
        # 换行在摘要里没有意义，折成空格——但这只发生在摘要里，
        # diff 和正文各自按原样处理。
        return _clip(" ".join(value.split()), limit)
    if isinstance(value, (bool, int, float)) or value is None:
        return repr(value)
    return _clip(json.dumps(value, ensure_ascii=False), limit)


def _clip(text: str, limit: int) -> str:
    """截断一段文字，并说明还有多少没显示。"""
    if len(text) <= limit:
        return text
    return f"{text[:limit]}…（后面还有 {len(text) - limit} 个字符未显示）"


# ---------------------------------------------------------------------- diff


def unified_diff(before: str, after: str, *, path: str, context: int = 2) -> str:
    """用 difflib 画一段 unified diff。

    两边一模一样时返回空串：一次没有改动的编辑不该在屏幕上留下任何东西，
    否则「有没有改」这件事就得靠人再去读一遍文件才能确认。

    返回的是若干行、不带结尾换行，方便调用方自己决定怎么画这每一行。
    """
    if before == after:
        return ""
    hunks = difflib.unified_diff(
        _diff_lines(before),
        _diff_lines(after),
        fromfile=path,
        tofile=path,
        lineterm="",
        n=context,
    )
    return "\n".join(hunks)


def _diff_lines(text: str) -> list[str]:
    """按行切开，并在「结尾没有换行」时补一个标记行。

    用 `splitlines()` 而不是 `split("\\n")`：后者在结尾换行时会多切出一个空串，
    于是每一份以换行结尾的文件都算「多了一行空行」，diff 里就凭空多出一条改动。
    反过来，结尾真的少了换行时 `splitlines()` 又完全看不出来——那不是
    「没有改动」，所以补一行 `\\ No newline at end of file` 把它标出来，
    否则「只补了结尾换行」这种改动会显示成一片空白。
    """
    if not text:
        return []
    lines = text.splitlines()
    if not text.endswith(("\n", "\r")):
        lines.append(_NO_NEWLINE)
    return lines


def _collapse(lines: list[str]) -> list[str]:
    """头 + 尾，中间那截换成一行「省略了多少行」。"""
    if not lines:
        return []
    clipped = [_clip(line, RESULT_LINE_CHARS) for line in lines]
    limit = RESULT_HEAD_LINES + RESULT_TAIL_LINES
    if len(clipped) <= limit + 1:
        # 只省一两行的话，那句「省略 N 行」比省掉的内容还占地方。
        return clipped
    omitted = len(clipped) - limit
    return [
        *clipped[:RESULT_HEAD_LINES],
        f"…（省略 {omitted} 行）…",
        *clipped[-RESULT_TAIL_LINES:],
    ]


def result_glyph(ok: bool, blocked: bool) -> str:
    """结果行的字形。

    `blocked` 单独成一档，因为它不是失败：工具根本没跑，是策略说不跑。
    这两种在屏幕上必须能分开，人看到才知道该去改代码还是改策略。
    """
    if blocked:
        return BLOCKED_GLYPH
    return OK_GLYPH if ok else FAILED_GLYPH


def _card(body: str) -> str:
    """工具卡里的一行：左边缘是竖线，所以「这一行属于上面那次调用」看得见。"""
    return f"{CARD_BODY} {body}" if body else CARD_BODY


def short_session(session_id: str, keep: int = 8) -> str:
    """会话 id 只取尾巴。完整 id 是给日志用的，屏幕上够区分两个会话就行。"""
    if not session_id:
        return "（未开始）"
    return session_id[-keep:] if len(session_id) > keep else session_id


# ---------------------------------------------------------------------- 行


def block_lines(marker: str, text: str) -> list[str]:
    """带前缀的一段文字。多行时后续行缩进对齐，免得看着像新起了一段。"""
    lines = text.splitlines() or [""]
    pad = " " * len(marker)
    return [marker + lines[0], *[pad + line for line in lines[1:]]]


def banner_lines(*, model: str, session_id: str, cwd: str, tools: int) -> list[str]:
    """开场那几行：现在是什么模型、哪个会话、在哪个目录、有几件工具。"""
    return [
        "DugentX",
        f"  model={model or '（未指定）'}  session={short_session(session_id)}  tools={tools}",
        f"  cwd={cwd or '（未指定）'}",
    ]


def step_line(index: int) -> str:
    """一次模型请求的边界。`step N` 对应循环里的一次请求，不是装饰。

    画成一条横线而不是一个方框里的标签：它是**分节**，不是一句内容——
    一眼扫过去要能看出「从这里开始是新的一步」，而不是又读到一个词。
    """
    return f"{RULE} step {index}"


def tool_call_line(name: str, arguments: dict[str, Any]) -> str:
    """模型要调一个工具。这一行开一张卡，只给要点，不搬正文。"""
    summary = summarize_arguments(name, arguments)
    return f"{CARD_TOP} {name}" + (f"  {summary}" if summary else "")


def tool_result_lines(*, name: str, content: str, ok: bool, blocked: bool) -> list[str]:
    """工具跑完了：一行结论，加上被折叠过的输出。

    被拦下的那一行多写一个词：`⊘` 和 `✗` 只差一个字形，而这件事的后果是
    「没执行」和「执行失败」——那两种要去的地方不一样，不该让人去背字形表。
    """
    glyph = result_glyph(ok, blocked)
    head = f"{glyph} {name}" + ("（被拦下）" if blocked else "")
    return [_card(head), *[_card(f"  {line}") for line in _collapse(content.splitlines())]]


def diff_lines(*, path: str, before: str, after: str) -> list[str]:
    """一段改动。两边一样就是空列表——屏幕上什么都不该多。

    不套进工具卡里：diff 自己带 `+` / `-` / `@@` 这些行首标记，而 `classify()`
    就是按它们判语气的。再套一层 `│` 会把那套标记挤到第二位，上色和折叠
    跟着一起失效——为了「看着整齐」破坏一份已经成立的约定，不划算。
    """
    text = unified_diff(before, after, path=path)
    return [] if not text else text.split("\n")


def notice_line(text: str, *, kind: str = "info") -> str:
    """一行旁白。warn / error 带字形前缀：颜色关掉之后，这两类必须还能被
    一眼认出来；其余种类靠文字自己说清楚，不加前缀。"""
    tag = {"warn": f"{WARN_GLYPH} ", "error": f"{FAILED_GLYPH} "}.get(kind, "")
    return tag + text


def error_line(text: str) -> str:
    return f"{FAILED_GLYPH} {text}"


def question_lines(question: Question) -> list[str]:
    """画出一次提问：先问，再给上下文，最后列出每一个键。

    选项带上 `[key]` 是有意的——没有颜色的时候，人得知道该按哪个键；
    默认项也标出来，因为回车是最常见的输入。
    """
    lines = [f"{ASK_GLYPH} {question.prompt or '（空问题）'}"]
    lines.extend(f"  {line}" for line in question.detail.splitlines())
    if question.choices:
        shown = "  ".join(
            f"[{choice.key}] {choice.label}" + ("（默认）" if choice.is_default else "")
            for choice in question.choices
        )
        lines.append(f"  {shown}")
    return lines


def turn_end_line(result: TurnResult) -> str:
    """一个回合的收尾：一行说清它是怎么结束的、花了多少。

    和 `step_line` 用同一条横线：两者都是边界，差别只在粗细——
    一个回合是这一整屏里最大的一格。
    """
    return (
        f"{RULE} turn {result.stop_reason}  steps={result.steps}"
        f"  tools={result.tool_calls}  tokens={result.usage.total_tokens}"
    )


def status_line(
    *,
    model: str,
    session_id: str,
    steps: int,
    tokens: int,
    permission: str = "",
) -> str:
    """一行状态。

    每一项都写出来，包括「还不知道」：一个空着的字段看起来像「没有这一项」，
    而它其实是「这一项还没读到」。两者在屏幕上必须不一样。
    """
    parts = [
        model or "（未指定）",
        f"session={short_session(session_id)}",
        f"steps={steps}",
        f"tokens={tokens}",
    ]
    if permission:
        parts.append(f"权限={permission}")
    return "  ".join(parts)


def classify(line: str) -> str:
    """给一行定语气 —— 界面据此上色，纯文本形态下它什么都不影响。

    按前缀判断，因为这些前缀本来就不是装饰：它们是「这句话是谁说的」在没有
    颜色时的唯一线索。所以上色规则不另立一套，就长在这些前缀上——加一种行，
    只需要在一个地方说它是什么语气。字形表（模块开头）和这张表是同一张表。

    返回的是语气名，不是颜色：`error` 在两个界面上可以是不同的红，
    但「这是一条错误」只有一种答案。
    """
    if line.startswith(("---", "+++", "@@")):
        return "diff_meta"
    if line.startswith("+"):
        return "diff_add"
    if line.startswith("-"):
        return "diff_del"
    if line.startswith(USER_MARKER):
        return "user"
    if line.startswith(ASSISTANT_MARKER):
        return "assistant"
    if line.startswith(CARD_TOP):
        return "tool"
    # 卡片正文里，结果那一行按结果上色，其余是细节。顺序不能反：
    # 「竖线」是这一族里最宽的那一类，先判它，就等于所有正文行一个颜色。
    if line.startswith(f"{CARD_BODY} {OK_GLYPH}"):
        return "info"
    if line.startswith(f"{CARD_BODY} {FAILED_GLYPH}"):
        return "error"
    if line.startswith(f"{CARD_BODY} {BLOCKED_GLYPH}"):
        return "warn"
    # 卡片里剩下的都是细节（输出正文），落到最后的 dim 上——不另写一条分支：
    # 一条永远和兜底同值的分支，只会让人以为它和兜底不一样。
    if line.startswith(f"{WARN_GLYPH} "):
        return "warn"
    if line.startswith(f"{FAILED_GLYPH} "):
        return "error"
    if line.startswith(f"{ASK_GLYPH} "):
        return "info"
    if line.startswith(f"{RULE} step"):
        return "info"
    if line.startswith(f"{RULE} turn"):
        return "dim"
    return "dim"


# ---------------------------------------------------------------------- 转圈


def spinner_frame(index: int, *, animate: bool = True) -> str:
    """转圈的第 `index` 帧。`animate=False` 退化成**静止**的那一帧。

    「静止」不是为了好看：`--once`、CI、以及任何不该有动画的地方，
    一个自己在变的字形是纯粹的噪声，而静下来的那一帧仍然说出了同一件事
    ——这里在干活。
    """
    if not animate:
        return SPINNER_STATIC
    return SPINNER_FRAMES[index % len(SPINNER_FRAMES)]


def elapsed_text(seconds: float) -> str:
    """已经跑了多久。一位小数就够：这是一个感觉，不是计量。"""
    return f"{seconds:.1f}s"


def busy_text(*, frame: int, elapsed: float, animate: bool = True) -> str:
    """状态条最前面那一截：转着的帧，加上已经跑了多久。

    「跑了 4.2 秒」和状态条上别的事实有一个区别：它不是某个事件带来的，它是
    一个**钟**读出来的数。所以它可以由定时器驱动，而别的事实不行（见
    `paint.py` 和 `widgets.StatusBar` 的说明）。
    """
    return f"{spinner_frame(frame, animate=animate)} {elapsed_text(elapsed)}"


# ---------------------------------------------------------------------- 状态


@dataclass(frozen=True, slots=True)
class StatusUpdate:
    """状态条的一次**局部**更新：只带这次知道的那几项，其余保持不动。

    为什么不整条重画：`llm/usage` 只知道 tokens，`model/switched` 只知道模型，
    让每个事件都去凑齐全部字段，只会逼着它去猜自己不知道的那些。

    `busy` 是这一批里唯一**不是事实**的一项：它说的是「此刻有没有活干」，
    而它带来的那两个东西（转圈的帧、已经跑了多久）是钟读出来的数，不是事件
    带来的。所以它单独一项，而且只有回合的边界会动它（见 `paint.py`）——
    事实归事件，时间归钟，这条界线在 `widgets.StatusBar` 上写清楚了。
    """

    model: str | None = None
    session_id: str | None = None
    steps: int | None = None
    tokens: int | None = None
    permission: str | None = None
    busy: bool | None = None


class Renderer(Protocol):
    """渲染目的地。两条路各自实现它：`PlainRenderer` 与界面上的流水账。

    方法名就是「发生了什么」，不是「画到哪儿」——所以同一个画师
    （`paint.py`）可以驱动真终端界面，也可以驱动一段纯文本。
    """

    def banner(self, *, model: str, session_id: str, cwd: str, tools: int) -> None: ...

    def user(self, text: str) -> None: ...

    def assistant_delta(self, text: str) -> None: ...

    def assistant_end(self) -> None: ...

    def step(self, index: int) -> None: ...

    def tool_call(self, name: str, arguments: dict[str, Any]) -> None: ...

    def tool_result(self, *, name: str, content: str, ok: bool, blocked: bool) -> None: ...

    def diff(self, *, path: str, before: str, after: str) -> None: ...

    def notice(self, text: str, *, kind: str = "info") -> None: ...

    def error(self, text: str) -> None: ...

    def question(self, question: Question) -> None: ...

    def answer(self, text: str) -> None: ...

    def turn_end(self, result: TurnResult) -> None: ...


class PlainRenderer:
    """非交互路径的渲染器：每个方法往一个文本流写行。

    **不上色。** 颜色是界面的事：这一条路的输出会进管道、进日志、进 CI 的记录，
    往里面塞 ANSI 的人得自己负责。界面上那一份由 textual 上色，两边共用的
    只有排版（本模块的函数）——所以「谁看到什么」不会有第二种答案。
    """

    def __init__(self, stream: IO[str] | None = None) -> None:
        self.stream: IO[str] = stream if stream is not None else sys.stdout
        self._streaming = False

    # ---------------------------------------------------------------- 会话

    def banner(self, *, model: str, session_id: str, cwd: str, tools: int) -> None:
        self._lines(banner_lines(model=model, session_id=session_id, cwd=cwd, tools=tools))

    def user(self, text: str) -> None:
        """人说的话。原样画出来，一个字不改。"""
        self._lines(block_lines(USER_MARKER, text))

    def assistant_delta(self, text: str) -> None:
        """追加一块流式文本，不换行。

        第一块带前缀，后面的接在同一行上：一次回答是一行（或由模型自己换行
        决定的多行），不是每个 token 一行。
        """
        if not text:
            return
        if not self._streaming:
            self._write(ASSISTANT_MARKER)
            self._streaming = True
        self._write(text)

    def assistant_end(self) -> None:
        """收尾：把这一行关掉。没开着就什么都不做，所以调两次是安全的。"""
        if not self._streaming:
            return
        self._write("\n")
        self._streaming = False

    def step(self, index: int) -> None:
        self._lines([step_line(index)])

    # ---------------------------------------------------------------- 工具

    def tool_call(self, name: str, arguments: dict[str, Any]) -> None:
        self._lines([tool_call_line(name, arguments)])

    def tool_result(self, *, name: str, content: str, ok: bool, blocked: bool) -> None:
        self._lines(tool_result_lines(name=name, content=content, ok=ok, blocked=blocked))

    def diff(self, *, path: str, before: str, after: str) -> None:
        self._lines(diff_lines(path=path, before=before, after=after))

    # ---------------------------------------------------------------- 旁白

    def notice(self, text: str, *, kind: str = "info") -> None:
        self._lines([notice_line(text, kind=kind)])

    def error(self, text: str) -> None:
        self._lines([error_line(text)])

    # ---------------------------------------------------------------- 问人

    def question(self, question: Question) -> None:
        self._lines(question_lines(question))

    def answer(self, text: str) -> None:
        """把人刚才答的那一句回显出来。

        回答必须留下痕迹：审批过的东西在屏幕上没有任何记录，事后就没人说得清
        当时到底同意了哪一条。
        """
        self._lines(block_lines(ANSWER_MARKER, text))

    # ---------------------------------------------------------------- 收尾

    def turn_end(self, result: TurnResult) -> None:
        self._lines([turn_end_line(result)])

    # ---------------------------------------------------------------- 内部

    def _write(self, text: str) -> None:
        """写一段文字，并且立刻刷新。

        刷新不是可选的：流式回答是一块一块来的，最后一块之前没有换行，
        而指向终端的文本流是行缓冲的——不刷新，回答在屏幕上就是冻住的。
        """
        self.stream.write(text)
        self.stream.flush()

    def _lines(self, lines: Iterable[str]) -> None:
        # 画别的东西之前先把没写完的助手行收掉：不收，一个还没换行的正文行会
        # 和下一条 `[tool]` 挤在同一行里，看起来就像那次调用是那句话的一部分。
        self.assistant_end()
        for line in lines:
            self._write(line + "\n")
