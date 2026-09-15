"""事件目录。

dsh 用 TypeScript 的声明合并 + 生成的 catalog 来保证「声明的派发方式」
和「实际调用的方式」一致。DugentX 用一个更简单、但同样硬的机制：

> `EventBus` 拿到这份目录，**在派发时校验**。
> 用 `emit` 发一个声明为 waterfall 的事件会当场报错；
> 发一个目录里没有的事件名也会报错。

为什么要这么严？因为写错事件名是**静默失败**：监听器永远不触发，
没有异常，没有日志，只有「怎么没生效」。把它变成装载/派发时的错误，
是这个示例项目最想示范的一件事——护栏要在代码里，不在注释里。
"""

from __future__ import annotations

from dugentx.kernel.events import EventSpec

EVENTS: list[EventSpec] = [
    # ---------------------------------------------------------- 生命周期
    #
    # 这里**没有** session/start 和 session/end。它们是**会话日志的种类**
    # （`seams/session.py` 的 EventKind），不是总线事件。
    #
    # 一个会话开始了，是日志里记下的一条事实；没有谁需要「拦下」它，
    # 也没有谁需要当场反应——想读它就读日志。把它们摆进总线目录会造出
    # 一个永远不触发的监听器，而那种钩子比一个当场报错的名字难查得多。
    #
    # turn/start 这类则是**两边都有**：边界既值得记进日志，也可能有人要
    # 当场反应（界面画一条分隔线、统计步数）。
    EventSpec("turn/start", "emit", "一个回合开始", ["prompt"]),
    EventSpec("turn/end", "emit", "一个回合结束", ["result"]),
    EventSpec("step/start", "emit", "一次模型请求开始", ["index"]),
    EventSpec("step/end", "emit", "一次模型请求结束", ["index", "tool_calls"]),
    # ---------------------------------------------------------- 中间件
    EventSpec(
        "agent/pre-step",
        "waterfall",
        "决定这一步收哪些输入；可以改写，也可以拒绝",
        ["messages"],
    ),
    EventSpec(
        "agent/request",
        "waterfall",
        "模型请求发出前的最后一道；改写请求就挂这里",
        ["request"],
    ),
    EventSpec("llm/stream", "waterfall", "包住整条模型流", ["request", "stream"]),
    EventSpec(
        "tools/pre-execute",
        "waterfall",
        "工具执行前的策略层：权限、白名单、参数校验、审计",
        ["call"],
    ),
    # 这里**故意没有** tools/execute。工具本身的执行是 pre-execute 那个瀑布的
    # terminal 处理器——只有它当「最内层」，`nxt()` 才有能力决定放不放行。
    # 想包住执行，就订阅 pre-execute（记得调 nxt()）或 post-execute；
    # 再开一个「执行」事件只会得到一个没人发、订阅了也不响的钩子。
    EventSpec(
        "tools/post-execute",
        "waterfall",
        "工具结果后处理：脱敏、截断、转成模型看得懂的形式",
        ["outcome"],
    ),
    # ---------------------------------------------------------- 观察
    EventSpec("llm/chunk", "emit", "收到一个流式增量", ["delta"]),
    EventSpec("llm/usage", "emit", "一次调用的开销", ["usage"]),
    EventSpec("model/switched", "emit", "运行期换了 provider 或模型", ["provider", "model"]),
    EventSpec("tool/call", "emit", "模型请求了一次工具调用", ["call"]),
    EventSpec("tool/result", "emit", "工具结果已回填", ["outcome"]),
    EventSpec("permission/skip", "emit", "这一档不需要问，直接过", ["request"]),
    EventSpec("permission/decided", "emit", "人给了答复", ["request", "decision"]),
    EventSpec("context/compacting", "emit", "开始压缩上下文", ["before"]),
    EventSpec("context/compacted", "emit", "压缩完成", ["result"]),
    EventSpec("plugin/mounted", "emit", "运行期挂载了一个插件", ["plugin", "row"]),
    EventSpec("plugin/unmounted", "emit", "运行期拔掉了一个插件", ["plugin"]),
    EventSpec("skill/catalog", "emit", "技能目录被读取", ["count"]),
    EventSpec("skill/loaded", "emit", "一个技能被拉进上下文", ["name", "tokens"]),
    EventSpec("human/asked", "emit", "有人被问了一句", ["question", "source"]),
    EventSpec("human/answered", "emit", "人回答了", ["question", "answer"]),
    EventSpec("subagent/start", "emit", "子 Agent 出发", ["request"]),
    EventSpec("subagent/end", "emit", "子 Agent 回来", ["result"]),
    EventSpec("agent/error", "emit", "循环里出了错，但没有崩", ["where", "error"]),
]

EVENT_MODES: dict[str, str] = {spec.name: spec.mode for spec in EVENTS}


def describe_events() -> str:
    """给 `dugentx events` 命令打印用的目录。"""
    lines = []
    for spec in EVENTS:
        payload = f"  ({', '.join(spec.payload)})" if spec.payload else ""
        lines.append(f"{spec.mode:<10} {spec.name:<22} {spec.summary}{payload}")
    return "\n".join(lines)
