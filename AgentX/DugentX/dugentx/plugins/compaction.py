"""compaction 插件 —— 超预算时压缩，并把「压了什么」写进日志。

这个插件只做一件事，但它是整个压缩设计里最难的一块：**把一次压缩的决定
翻译成日志能表达的形式**。

压缩不能改写日志——日志是唯一真相，只能追加。所以一次压缩在日志里留下的
是一条 `context/compacted` 事件：

    {"drop_before_seq": 12, "summary": "……", "dropped": 9, ...}

`derive_view(log)` 据此重建出模型真正看到的那段历史：丢掉 seq 小于
`drop_before_seq` 的模型可见事件，再把 `summary` 作为一条消息放回最前面。
于是压缩可审计（日志里看得见什么时候压过、丢了几条、摘要是什么）、
可回退（换一个预算重新压一次，旧决定自动被后一条覆盖），
而「模型可见 ⟺ 已记录」这条规则**依然成立**。

`encode_drop` 就是这个翻译器。它做两件事，缺一不可：

1. 把压缩结果切成（摘要，保留的那段尾巴）；
2. 要求那段尾巴**逐字等于**日志投影的一个后缀——因为日志只能表达
   「丢掉一段前缀」，任何「就地改写还留着的消息」都是日志重建不出来的。
   翻不动的时候它大声报错，而不是让模型看到一段日志里没有的历史。

翻译完还有一道校验：把事件写进日志之后，重新跑一遍 `derive_view`，
确认它和压缩结果对得上，再跑一遍 `verify_projection`。规则只在文档里
写一遍，三个月后就有人绕过它；写成这个函数里的两行断言，绕不过去。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from dugentx.kernel.context import Context
from dugentx.kernel.errors import DuGentXError, PluginError
from dugentx.kernel.plugin import define_plugin
from dugentx.providers.compaction_basic import BasicCompactor
from dugentx.seams.compaction import CompactionResult, messages_tokens
from dugentx.seams.messages import Message
from dugentx.seams.session import (
    MODEL_VISIBLE,
    SessionEvent,
    SessionLog,
    derive_view,
    verify_projection,
)

DEFAULT_BUDGET_TOKENS = 60_000
"""没写预算时的默认值。和 `AgentConfig.context_budget_tokens` 的默认值一致——
两处不一致会变成「配置里写着 60000，实际按 60000 压还是按另一个数压」这种查不动的问题。"""

PLUGIN_KEYS = frozenset({"context_budget_tokens"}) | BasicCompactor.CONFIG_KEYS


def encode_drop(
    log: SessionLog,
    before: list[Message],
    result: CompactionResult,
) -> tuple[int, str]:
    """把一次压缩的结果翻译成日志能表达的两个值：切点和摘要。

    约定：`result.summarized > 0` 表示结果的第一条就是摘要消息。这条约定是
    `CompactionResult` 那两个计数字段的用处——它们本来就是「给日志和用户看的」。

    翻译不出来就抛 `DuGentXError`。这里**没有**「尽力而为」的降级：
    一个无法从日志重建的投影，会让「模型可见 ⟺ 已记录」当场变成一句空话，
    而那种问题上线以后是查不出来的——模型的历史和日志对不上，两边都像是真的。
    """
    tail = list(result.messages)
    summary = ""
    if result.summarized > 0:
        if not tail:
            raise DuGentXError(
                "压缩结果说它写了摘要（summarized > 0），但消息列表是空的；"
                "这两个字段必须说同一件事"
            )
        summary = tail[0].content
        tail = tail[1:]

    events = _visible_events(log)
    base = _projection(events)
    if len(base) > len(before) or not _same(before[len(before) - len(base) :], base):
        raise DuGentXError(
            f"压缩的输入和日志对不上：日志现在投影出 {len(base)} 条，"
            f"而压缩器拿到的是 {len(before)} 条。压缩只能建立在 derive_view(log) 之上——"
            f"否则它保留下来的东西没有出处。"
        )

    if len(tail) > len(base):
        raise DuGentXError(
            f"压缩结果保留了 {len(tail)} 条消息，日志里只有 {len(base)} 条；"
            f"压缩不会凭空造出消息"
        )
    kept = base[len(base) - len(tail) :] if tail else []
    if not _same(kept, tail):
        raise DuGentXError(
            "这次压缩的结果无法从日志重建：日志只能表达「丢掉一段前缀」，"
            "不能就地改写或重排还留着的消息。压缩器改写了保留区里的消息，"
            "或者把前后顺序调过了——请让压缩只丢前缀，把要保留的信息放进摘要。"
        )

    cutoff = events[len(base) - len(tail)].seq if tail else _past_the_end(events)
    return cutoff, summary


def _visible_events(log: SessionLog) -> list[SessionEvent]:
    """当前生效的那批模型可见事件（已经套用上一次压缩的切点）。

    必须套用上一次的切点，否则第二条压缩事件的 `seq` 会以「还没压过」的
    视角算出来，两次压缩的切点就不可比了。
    """
    previous = log.last("context/compacted")
    cutoff = int(previous.data.get("drop_before_seq", 0)) if previous is not None else 0
    return [event for event in log.events() if event.kind in MODEL_VISIBLE and event.seq >= cutoff]


def _projection(events: Sequence[SessionEvent]) -> list[Message]:
    """借一个干净的临时日志，把一批事件投影成消息。

    走 `derive_view` 而不是自己去拼消息：投影规则只能有一份实现，
    复制一份出来就等于以后会有两份不一样的历史。
    """
    return derive_view(SessionLog(events=list(events)))


def _past_the_end(events: Sequence[SessionEvent]) -> int:
    return events[-1].seq + 1 if events else 0


def _same(left: Sequence[Message], right: Sequence[Message]) -> bool:
    if len(left) != len(right):
        return False
    return all(
        a.role == b.role
        and a.content == b.content
        and a.tool_call_id == b.tool_call_id
        and a.tool_calls == b.tool_calls
        for a, b in zip(left, right, strict=True)
    )


# ------------------------------------------------------------------ 监听器


def make_pre_step_listener(ctx: Context, compactor: BasicCompactor, budget: int) -> Any:
    """造出 `agent/pre-step` 上的那个监听器。

    它在瀑布里**包住里面所有监听器**：先让它们改完，再量一次，超了才压。
    顺序反过来的话，压完又被别人塞回来，预算是按压之前量的，等于没压。

    另外一件必须说清楚的事：这里的输入理论上只可能有四种角色的消息，
    而 `derive_view` 永远不会投影出 `system` 角色——system 提示词不进日志。
    所以一旦在投影里看到 system 消息，说明有插件往请求里塞了没记录的东西；
    这时候压缩**拒绝工作**，因为丢掉 system 前缀等于换了一个人在说话，
    而它一旦被丢掉，日志上看不出任何异常。
    """

    async def on_pre_step(messages: list[Message], *extra: Any) -> list[Message]:
        # 瀑布保证最后一个是 next；前面可能还带着别的负载（现在的循环只传
        # messages，但把 agent 一起传进来是合理的演进方向，所以两种都接受）。
        nxt = extra[-1]
        agent = next(
            (item for item in extra[:-1] if hasattr(item, "config") and hasattr(item, "session")),
            None,
        )
        current = await nxt(messages)
        if not isinstance(current, list):
            return current

        session: SessionLog = agent.session if agent is not None else ctx.service("session")
        limit = int(
            getattr(getattr(agent, "config", None), "context_budget_tokens", 0) or budget
        )
        before = messages_tokens(current)
        if before <= limit:
            return current

        if any(message.role == "system" for message in current):
            raise DuGentXError(
                "agent/pre-step 的投影里有 system 消息，而会话日志投影不出 system 角色——"
                "说明有插件往请求里塞了没记录的东西。压缩在这种状态下拒绝工作："
                "丢掉 system 前缀等于换了一个人在说话，而日志上看不出来。"
            )

        ctx.events.emit("context/compacting", before)
        result = await compactor.compact(
            ctx, current, budget_tokens=limit, model=_model_hint(ctx, session, agent)
        )
        if not result.changed:
            # 压不动了。宁可让它超着发出去，也不要伪造一次「压过了」——
            # 记录一次没有发生的压缩，比超预算难查得多。
            return current

        cutoff, summary = encode_drop(session, current, result)
        session.append(
            "context/compacted",
            drop_before_seq=cutoff,
            summary=summary,
            dropped=result.dropped,
            summarized=result.summarized,
            before_tokens=result.before_tokens,
            after_tokens=result.after_tokens,
            budget_tokens=limit,
            note=result.note,
        )
        final = _verified(session, result, summary)
        ctx.events.emit("context/compacted", result)
        return final

    return on_pre_step


def _verified(session: SessionLog, result: CompactionResult, summary: str) -> list[Message]:
    """事件已经写进日志之后，确认模型将要看到的东西确实能从日志重算出来。"""
    after = derive_view(session)
    tail = result.messages[1:] if summary else list(result.messages)
    if summary:
        if not after or not after[0].content.endswith(summary):
            raise DuGentXError(
                "压缩事件写进日志了，但 derive_view 重算出来的第一条不是这次写的摘要；"
                "摘要必须整段落在事件里，不能只存个大概"
            )
        after_tail = after[1:]
    else:
        after_tail = after
    if not _same(after_tail, tail):
        raise DuGentXError(
            f"压缩结果和日志投影对不上（结果留了 {len(tail)} 条，"
            f"日志重算出 {len(after_tail)} 条）。"
            f"这说明这次压缩不能被日志重建——模型会看到一段日志里没有的历史。"
        )
    # 到这一步它必然成立（刚刚逐条比过），留在这里是为了让规则在代码里有个落点：
    # 每个 step 之前都要过这一关，压缩不许开后门。
    verify_projection(after, session)
    return after


def _model_hint(ctx: Context, session: SessionLog, agent: Any) -> str:
    """摘要该用哪个模型。

    优先用发起这次压缩的那个 agent 的模型；拿不到就去 agent 名单里按会话号找。
    `agents` 是软依赖（本插件只声明 inject=("session",)），拿不到就不猜——
    退到机械摘录，而不是用一个不知道对不对的模型名去发一次请求。
    """
    if agent is not None:
        return str(getattr(agent.config, "model", "") or "")
    registry = ctx.get("agents")
    getter = getattr(registry, "get", None)
    if getter is None:
        return ""
    found = getter(session.session_id)
    return str(getattr(getattr(found, "config", None), "model", "") or "")


# ------------------------------------------------------------------ 插件


@define_plugin(
    "compaction",
    inject=("session",),
    provides=("compaction",),
    description="上下文压缩：超预算时把早期消息压成一条摘要，并把这次决定写进日志",
)
def create(ctx: Context, config: dict[str, Any]) -> None:
    """装载压缩器，并把它挂到 `agent/pre-step` 上。

    这个插件只声明依赖 `session`：压缩要读日志、要把决定写回日志，
    而日志是它唯一必须认识的东西。模型（`llm`）和 agent 名单都是**软依赖**——
    有就用它们写更好的摘要，没有就退到机械摘录。依赖声明得越窄，
    这个插件能装的位置就越多：一个没有配任何模型的离线组合，压缩照样跑得起来。
    """
    unknown = set(config) - PLUGIN_KEYS
    if unknown:
        raise PluginError(
            f"compaction 配置里有不认识的键：{sorted(unknown)}；可用：{sorted(PLUGIN_KEYS)}"
        )
    budget = int(config.get("context_budget_tokens", DEFAULT_BUDGET_TOKENS))
    if budget <= 0:
        raise PluginError(f"context_budget_tokens 必须是正数，收到 {budget}")

    compactor = BasicCompactor.from_config(
        {key: value for key, value in config.items() if key != "context_budget_tokens"}
    )
    ctx.provide("compaction", compactor)
    ctx.on("agent/pre-step", make_pre_step_listener(ctx, compactor, budget))
