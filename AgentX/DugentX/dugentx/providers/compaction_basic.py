"""compaction 缝的默认 provider —— 三种丢东西的办法，和一条不许碰的底线。

上下文有预算，跑久了必然超。压缩就是决定「丢掉什么」，而每一种丢法
都是在不同的东西上让步：

- **剪枝（prune）**：对准旧的**工具结果**。它们通常最大、也最不承重——
  一次 `ls` 的完整输出、一个文件的前四百行，读过之后只剩下结论有用。
  剪枝把这种「完整的工具交换」整段丢出窗口，并在摘要材料里留下
  「调用了什么、结果开头是什么」，调用本身也就还有痕迹。
- **摘要（summarize）**：把丢掉的那一段交给模型写成一段摘要，提示词要求它
  保住**决定、文件路径、还没解决的错误**。没有可用的模型时退到机械摘录
  （每条取开头，并明确标出这是摘录不是摘要），而不是失败——压缩失败会让
  整轮请求发不出去，那比摘要难看严重得多。
- **截断（truncate）**：什么便宜招都不管用了，就丢最早的消息。它是唯一
  一定会丢信息的机制，所以排最后，而且必须在 `note` 里说出来。

三者的顺序是固定的，理由也是具体的：剪枝不花钱（没有模型调用），先做；
摘要要花钱，只在剪枝不够时才做；截断一定会丢信息，最后做。

一条底线：**system 前缀永不参与压缩**。它是这一次请求的人格与硬规矩
（「不要动生产配置」就写在这里），把它丢掉，模型就换了一个人在说话，
而日志上什么都看不出来。

还有一件实现上的事必须说清楚：本 provider 只能**丢掉一段前缀**，
不能改写留下来的那些消息——把一条四千 token 的工具结果就地点成一百 token
是做不到的，因为日志只能追加，而模型看到的必须是日志能重算出来的东西。
所以剪枝被表达成「整段丢掉 + 把调用身份和结果开头写进摘要」。内容少了一条
路径，但没有少内容：`encode_drop`（在 `plugins/compaction.py`）会把
「压缩结果 → 日志能表达的形式」这一步翻译出来，翻不动就大声报错。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from dugentx.kernel.context import Context
from dugentx.kernel.errors import DuGentXError, PluginError
from dugentx.seams.compaction import (
    CompactionResult,
    messages_tokens,
)
from dugentx.seams.llm import LlmRequest, drain_stream
from dugentx.seams.messages import Message


def assert_tool_pairing(messages: Sequence[Message]) -> None:
    """断言没有「找不到调用的工具结果」。

    这条不变量不是洁癖。一条 `tool` 消息如果没有对应的 assistant 工具请求，
    provider 会拒掉**整个请求**——不是丢掉那一条，是这一轮根本发不出去，
    而报错信息通常只说「tool_call_id 无效」，看不出来是压缩干的。
    压缩最容易犯这个错：丢掉了 assistant 的调用，留下了它的结果。
    所以这里宁可当场炸，也不要把一个发不出去的请求交给模型。
    """
    known = {call.id for message in messages for call in message.tool_calls}
    orphans = [
        message.tool_call_id
        for message in messages
        if message.role == "tool" and message.tool_call_id not in known
    ]
    if orphans:
        raise DuGentXError(
            f"压缩后留下了 {len(orphans)} 条找不到调用的工具结果（tool_call_id="
            f"{orphans[:3]}）。这种请求 provider 会整条拒掉，所以压缩不许产出它："
            f"丢掉一次工具调用时，它的结果必须一起丢。"
        )


# ------------------------------------------------------------------ 材料


PRIOR_SUMMARY_HINT = "【以下是更早对话的摘要"
"""`derive_view` 给上一次压缩的摘要加的标记。"""

EXTRACTIVE_HINT = "【以下是上下文压缩丢掉的"
"""机械摘录自己的抬头。和上一条一样，认出来是为了在带下去的时候剥掉它——
摘要套摘要、摘录套摘录，套到第三层就没人看得懂了。"""


SUMMARY_SYSTEM_PROMPT = (
    "你在为一次长时间的对话做上下文压缩。下面这段记录即将被丢弃，"
    "请把它压成一段可以直接放回对话开头的摘要。\n"
    "必须保留：\n"
    "1. 已经做出的决定，以及做这个决定的理由；\n"
    "2. 出现过的文件路径、命令、函数名，原样照抄，不要改写、不要概括；\n"
    "3. 还没解决的错误和失败，包括报错信息和它出现的位置。\n"
    "不要保留：寒暄、重复的确认、已经被推翻的中间结论。\n"
    "用中文写，不要加标题，不要复述这段要求。"
)
"""摘要提示词。三条要求对应三种「丢了就得重做」的东西：
决定是方向，路径是位置，未解决的错误是债务。"""


def _short_args(arguments: dict[str, Any], limit: int = 120) -> str:
    text = ", ".join(f"{key}={value!r}" for key, value in arguments.items())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _first_lines(text: str, keep_lines: int, keep_chars: int) -> str:
    """留下开头若干行，其余换成一行的说明。

    行数和字数两个上限都要有：工具结果里经常混着一条超长的行（压缩过的
    JSON、base64），只按行数截会留下一个和原文一样大的开头。
    """
    lines = text.splitlines()
    head = "\n".join(lines[:keep_lines])
    if len(lines) > keep_lines:
        head += f"\n…（后面还有 {len(lines) - keep_lines} 行，已裁掉）"
    if len(head) > keep_chars:
        head = head[: keep_chars - 1] + "…"
    return head


def _first_sentence(text: str, limit: int) -> str:
    body = " ".join(text.strip().split())
    if not body:
        return ""
    for mark in ("。", "！", "？", ". "):
        index = body.find(mark)
        if 0 <= index < limit:
            return body[: index + 1]
    return body if len(body) <= limit else body[: limit - 1] + "…"


def _strip_summary_frames(text: str) -> str:
    """剥掉摘要外层的那些标记行，只留下内容。"""
    body = text
    for hint in (PRIOR_SUMMARY_HINT, EXTRACTIVE_HINT):
        if body.startswith(hint):
            body = body.split("\n", 1)[-1] if "\n" in body else ""
    return body.strip()


def render_span(
    dropped: Sequence[Message],
    *,
    keep_lines: int,
    keep_chars: int,
    carry_chars: int,
) -> list[str]:
    """把丢掉的那一段渲染成一份紧凑材料。

    同一份材料有两个用处：机械摘录直接用它，摘要模型收到的也是它。
    这不是省事，而是为了消掉第三种状态——「剪枝剪掉了、但摘要里也没有」。
    材料里的工具结果永远是「调用身份 + 开头若干行」，所以剪枝和摘要是
    对同一份东西做的两件事，不是两个各自的决定。
    """
    calls: dict[str, str] = {}
    lines: list[str] = []
    for message in dropped:
        for call in message.tool_calls:
            calls[call.id] = f"{call.name}({_short_args(call.arguments)})"

        if message.content.startswith((PRIOR_SUMMARY_HINT, EXTRACTIVE_HINT)):
            # 上一次压缩的成果**整段**带下去。取首句是不够的：那等于把摘要
            # 当成一条普通旧消息丢掉，信息在无声无息中消失——这是压缩最常
            # 见的坑，而它表面上一切正常。
            carried = _strip_summary_frames(message.content)[:carry_chars]
            lines.append(f"[上一次的摘要，整段带下来] {carried}")
        elif message.role == "tool":
            who = calls.get(message.tool_call_id or "", "（调用已经不在窗口里）")
            head = _first_lines(message.content, keep_lines, keep_chars)
            lines.append(f"[工具结果 {who} 的开头] {head}")
        elif message.tool_calls and not message.content.strip():
            asked = "、".join(calls[call.id] for call in message.tool_calls)
            lines.append(f"[助手请求调用] {asked}")
        else:
            lines.append(f"[{message.role}] {_first_sentence(message.content, keep_chars)}")
    return [line for line in lines if line.split("] ", 1)[-1].strip()]


def extractive_summary(lines: Sequence[str], count: int) -> str:
    """没有模型可用时的机械摘录。

    它明确写出自己是什么、丢了什么。压缩最不该做的事情是假装自己没发生：
    一段看起来很连贯的假摘要，比一段明说是摘录的东西危险得多——
    前者会被当成事实，后者会让人去翻日志。
    """
    head = (
        f"{EXTRACTIVE_HINT} {count} 条早期消息的机械摘录；"
        "没有可用的模型来写摘要，所以每条只留开头，工具结果只留开头若干行】"
    )
    return head + "\n" + "\n".join(f"- {line}" for line in lines)


# ------------------------------------------------------------------ 压缩器


class BasicCompactor:
    """`ctx.compaction` 的默认实现：剪枝 → 摘要 → 截断。"""

    CONFIG_KEYS = frozenset(
        {
            "prune_tool_results",
            "keep_recent_tool_lines",
            "summarize",
            "truncate",
            "keep_recent_messages",
            "target_ratio",
            "summarize_model",
            "summarize_chars",
            "carry_chars",
        }
    )
    """本 provider 认识的配置键。**不认识就报错**：一个拼错的键如果被静默
    忽略，表现出来是「我明明配了，怎么没生效」，那是最花时间的一类问题。"""

    def __init__(
        self,
        *,
        prune_tool_results: bool = True,
        keep_recent_tool_lines: int = 12,
        summarize: bool = True,
        truncate: bool = True,
        keep_recent_messages: int = 6,
        target_ratio: float = 0.6,
        summarize_model: str = "",
        summarize_chars: int = 160,
        carry_chars: int = 2000,
    ) -> None:
        if keep_recent_messages < 1:
            raise PluginError(
                "keep_recent_messages 至少要是 1：最近的一条消息都丢掉，等于这一轮没有输入"
            )
        if not 0 < target_ratio <= 1:
            raise PluginError(f"target_ratio 必须在 (0, 1] 之间，收到 {target_ratio}")
        self.prune_tool_results = prune_tool_results
        self.keep_recent_tool_lines = max(1, keep_recent_tool_lines)
        self.summarize = summarize
        self.truncate = truncate
        self.keep_recent_messages = keep_recent_messages
        self.target_ratio = target_ratio
        self.summarize_model = summarize_model
        self.summarize_chars = summarize_chars
        self.carry_chars = carry_chars

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> BasicCompactor:
        """从配置行里长出一个压缩器。未知键在这里就报错，不留到运行期。"""
        unknown = set(config) - cls.CONFIG_KEYS
        if unknown:
            raise PluginError(
                f"compaction 配置里有不认识的键：{sorted(unknown)}；"
                f"可用：{sorted(cls.CONFIG_KEYS)}"
            )
        return cls(
            prune_tool_results=bool(config.get("prune_tool_results", True)),
            keep_recent_tool_lines=int(config.get("keep_recent_tool_lines", 12)),
            summarize=bool(config.get("summarize", True)),
            truncate=bool(config.get("truncate", True)),
            keep_recent_messages=int(config.get("keep_recent_messages", 6)),
            target_ratio=float(config.get("target_ratio", 0.6)),
            summarize_model=str(config.get("summarize_model", "")),
            summarize_chars=int(config.get("summarize_chars", 160)),
            carry_chars=int(config.get("carry_chars", 2000)),
        )

    # ---------------------------------------------------------------- 主流程

    async def compact(
        self,
        ctx: Context,
        messages: list[Message],
        *,
        budget_tokens: int,
        model: str = "",
    ) -> CompactionResult:
        """把投影压进预算。

        `model` 是给摘要调用用的模型名，由调用方（装载它的那个插件）从当前
        agent 的配置里取。**取不到就不用模型**：不知道该用哪个模型时瞎猜一个，
        只会把一次预算问题变成一个认证错误。
        """
        if budget_tokens <= 0:
            raise DuGentXError(
                f"budget_tokens 必须是正数，收到 {budget_tokens}；"
                f"预算为 0 会让压缩丢掉一切，而它说不出为什么要这么做"
            )
        before = messages_tokens(messages)
        if before <= budget_tokens:
            return CompactionResult(
                messages=list(messages),
                before_tokens=before,
                after_tokens=before,
                note=f"没超预算（{before} ≤ {budget_tokens}），什么都没动",
            )

        assert_tool_pairing(messages)
        head, rest = _split_system_prefix(messages)
        target = max(1, int(budget_tokens * self.target_ratio))
        cut, why = self._choose_cut(head, rest, target)
        dropped = list(rest[:cut])
        if not dropped:
            return _untouched(messages, before, why)

        summary, how = await self._write_summary(ctx, dropped, model=model)
        out = _assemble(head, rest, cut, summary)
        after = messages_tokens(out)

        if summary and after >= before:
            # 摘要比它替代的内容还大，那它就不是压缩，是膨胀。常见于「只丢掉了
            # 一条很短的消息」：赚回来的还不如一句话的摘要贵。先把切点推到摘要
            # 也放得下的地方再试一次……
            room = max(1, target - messages_tokens([Message(role="user", content=summary)]))
            deeper, deeper_why = self._choose_cut(head, rest, room)
            if deeper > cut:
                dropped = list(rest[:deeper])
                summary, how = await self._write_summary(ctx, dropped, model=model)
                cut, why = deeper, deeper_why
                out = _assemble(head, rest, cut, summary)
                after = messages_tokens(out)
            if summary and after >= before:
                # ……还不够，就不要摘要了：只截断，并且把这件事写在 note 里。
                # 一次「压缩」把窗口做大，是这份代码最不该出现的结果。
                summary = ""
                how = "摘要比它替代的内容还大，所以这次只做了截断，没写摘要"
                out = _assemble(head, rest, cut, "")
                after = messages_tokens(out)

        if after >= before:
            return _untouched(messages, before, f"{why}；压完反而更大，所以这次什么都没动")

        assert_tool_pairing(out)
        note = f"{why}；{how}" if how else why
        return CompactionResult(
            messages=out,
            dropped=len(dropped),
            summarized=len(dropped) if summary else 0,
            before_tokens=before,
            after_tokens=after,
            note=note,
            kept_head=out[:3],
        )

    async def _write_summary(
        self, ctx: Context, dropped: Sequence[Message], *, model: str
    ) -> tuple[str, str]:
        """为被丢掉的那一段写摘要，返回（文本，用了哪种办法）。"""
        if not self.summarize or not dropped:
            return "", ""
        lines = render_span(
            dropped,
            keep_lines=self.keep_recent_tool_lines,
            keep_chars=self.summarize_chars,
            carry_chars=self.carry_chars,
        )
        if not lines:
            return "", ""
        return await self._summarize(ctx, lines, len(dropped), model=model)

    # ---------------------------------------------------------------- 丢到哪

    def _choose_cut(
        self,
        head: Sequence[Message],
        rest: Sequence[Message],
        target: int,
    ) -> tuple[int, str]:
        """决定丢掉 `rest` 的前几条，并给出一句人话说明。

        所有候选取值都必须是「安全的切点」：切完留下的第一条不能是 `tool`
        消息，否则就制造了那条会让整个请求被拒的孤儿结果。剪枝的切点更严：
        它必须落在一次工具结果的**后面**，这样丢掉的是一段段完整的工具交换，
        而不是把一问一答切成两半。

        选最小的、能压到目标以下的那个切点——丢得越少越好，这是压缩唯一
        不该讨价还价的地方。
        """
        head_tokens = messages_tokens(list(head))
        per_message = [messages_tokens([message]) for message in rest]
        suffix = [0] * (len(rest) + 1)
        for index in range(len(rest) - 1, -1, -1):
            suffix[index] = suffix[index + 1] + per_message[index]

        allowed = max(0, len(rest) - self.keep_recent_messages)
        prune_cuts = (
            [
                k
                for k in range(1, allowed + 1)
                if _is_safe_cut(rest, k) and rest[k - 1].role == "tool"
            ]
            if self.prune_tool_results
            else []
        )
        any_cuts = (
            [k for k in range(1, allowed + 1) if _is_safe_cut(rest, k)] if self.truncate else []
        )

        for k in sorted(set(prune_cuts) | set(any_cuts)):
            if head_tokens + suffix[k] <= target:
                kind = (
                    "剪枝：丢掉最早的几个完整工具交换"
                    if k in prune_cuts
                    else "截断：丢掉最早的消息"
                )
                return k, f"{kind}（丢 {k} 条，留 {len(rest) - k} 条）"

        if self.truncate:
            deeper = [k for k in range(allowed + 1, len(rest)) if _is_safe_cut(rest, k)]
            for k in deeper:
                if head_tokens + suffix[k] <= target:
                    return k, (
                        f"截断：剪枝不够，连最近的对话也开始丢（丢 {k} 条，留 {len(rest) - k} 条）"
                    )
            if deeper:
                return max(deeper), "截断：丢到只剩最后一条，仍然没到目标"
        if prune_cuts:
            return max(prune_cuts), "剪枝：已经到剪枝的边界，再往下就要动最近的对话，仍然没到目标"
        if any_cuts:
            return max(any_cuts), "截断：丢到保留线为止，仍然没到目标"
        return 0, "压不动：最近的对话不能被丢，只能让它超着发出去"

    # ---------------------------------------------------------------- 摘要

    async def _summarize(
        self,
        ctx: Context,
        lines: Sequence[str],
        count: int,
        *,
        model: str,
    ) -> tuple[str, str]:
        """写摘要，返回（文本，用了哪种办法）。第二种返回值会被记进日志。"""
        material = "\n".join(f"- {line}" for line in lines)
        wanted = model or self.summarize_model
        registry = ctx.get("llm")
        if registry is not None and wanted:
            try:
                adapter = registry.adapter()
                turn = await drain_stream(
                    adapter,
                    LlmRequest(
                        model=wanted,
                        messages=[
                            Message(role="system", content=SUMMARY_SYSTEM_PROMPT),
                            Message(role="user", content=material),
                        ],
                        temperature=0.2,
                    ),
                )
            except Exception as exc:  # 摘要失败不该让整轮请求发不出去
                ctx.events.emit("agent/error", where="compaction:summarize", error=exc)
            else:
                text = turn.content.strip()
                if text:
                    ctx.events.emit("llm/usage", turn.usage)
                    return text, "摘要是模型写的"
        return extractive_summary(lines, count), "没有可用模型，摘要是逐条取开头的机械摘录"


def _assemble(
    head: Sequence[Message],
    rest: Sequence[Message],
    cut: int,
    summary: str,
) -> list[Message]:
    """拼出压缩后的投影：system 前缀 + 摘要 + 保留的尾巴。

    摘要放在 system 前缀之后、其余消息之前——它替代的是那些被丢掉的早期消息，
    所以它必须出现在「更早」的位置上；而 system 前缀永远在最前面。
    """
    out = list(head)
    if summary:
        out.append(Message(role="user", content=summary))
    out.extend(rest[cut:])
    return out


def _untouched(messages: Sequence[Message], before: int, note: str) -> CompactionResult:
    """原样返回，并把「为什么没压成」写清楚。

    `dropped` / `summarized` 留 0，于是 `changed` 是假的——插件看到这一点就
    不会往日志里写一条没有发生过的压缩。记录一次假的压缩，比超预算难查得多。
    """
    return CompactionResult(
        messages=list(messages),
        before_tokens=before,
        after_tokens=before,
        note=note,
    )


def _is_safe_cut(rest: Sequence[Message], cut: int) -> bool:
    """切点是否安全：留下的一条不能是工具结果。

    这就是孤儿结果那道题的全部算法。`cut == len(rest)` 表示全丢，永远安全。
    """
    return cut >= len(rest) or rest[cut].role != "tool"


def _split_system_prefix(messages: Sequence[Message]) -> tuple[list[Message], list[Message]]:
    """切开「不许动的 system 前缀」和「其余」。

    前缀不是「优先级更高的几条消息」，而是**根本不参与压缩**：它是这一次
    请求的立宪文本。中间夹着的 system 消息是拼错了，这里直接报错——
    压缩无法保证一条夹在中间的系统消息不被丢掉，而它一旦被丢掉，
    模型换了一个人在说话，日志上却看不出来。
    """
    index = 0
    while index < len(messages) and messages[index].role == "system":
        index += 1
    for later in messages[index:]:
        if later.role == "system":
            raise DuGentXError(
                "system 消息只能连续出现在最前面；中间夹着一条 system，"
                "压缩无法保证它不被丢掉，所以拒绝在这个输入上工作"
            )
    return list(messages[:index]), list(messages[index:])
