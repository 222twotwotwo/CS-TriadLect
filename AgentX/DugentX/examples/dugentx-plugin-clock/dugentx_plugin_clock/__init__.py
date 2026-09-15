"""clock 插件包 —— 「一个插件就是一个 pip 装得上的包」的最小完整示例。

它给模型一个 `now` 工具。这不是玩具：**模型自己没有时钟**。问它「现在几点」，
它只能猜、或者从训练数据里推一个早已过期的答案。补上这个能力是 harness 该做的事，
而这个能力又天然独立于 DugentX 本体——正是「独立分发」最合适的例子：
装了就出现，不装就消失，本体一行都不用改。

一个插件包需要的全部东西，这个文件里都有：

1. **一把被 `define_plugin` 装饰过的工厂**。清单（要哪些服务、给什么、版本）
   挂在它上面，所以 DugentX 不装载它也能知道它是干什么的。
2. **`pyproject.toml` 里的一条 entry point**，组名是 `dugentx.plugins`。
   这是接入点：`pip install` 之后它就出现在 `dugentx plugins --available` 里。
3. **所有注册都走 `ctx.effect`**。这样插件被拔掉时，工具和提示词段落会
   一起消失——「注册必须能撤销」这条规则对第三方插件和对内核一样硬。

配置（`config:` 下面那几个键）在装载时校验：拼错的键名当场报错，
而不是安静地不生效。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from dugentx.kernel.context import Context
from dugentx.kernel.errors import PluginError
from dugentx.kernel.plugin import define_plugin
from dugentx.seams.prompt import static
from dugentx.seams.tools import LABEL_READ, tool_from_function

KNOWN_CONFIG = {"hint", "zone"}
"""这个插件认识的配置键。多一个都不认——见下面 `create` 里的校验。"""


@dataclass(slots=True)
class Clock:
    """`ctx.clock` —— 这台机器上的时间。

    做成一个服务而不是直接写进工具里，是因为「时间」是个会被别的东西需要的事实：
    审计日志要盖时间戳、压缩要判断会话有多旧、模型要回答「现在几点」。
    它们该读同一个来源，而不是各自去 `datetime.now()`。
    """

    zone: str = ""

    def stamp(self) -> str:
        """`2026-09-14 15:04:05`，带不带时区说明由配置决定。"""
        moment = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return f"{moment}（{self.zone}）" if self.zone else moment


def now(ctx: Context, what: str = "") -> str:
    """报告当前日期和时间。

    模型自己推算不了这个，所以要调工具。时间取的是运行 harness 的这台机器。

    Args:
        what: 想额外问的那部分时间，比如「星期几」。留空就只给完整时间。
    """
    clock: Clock = ctx.service("clock")
    stamp = clock.stamp()
    if not what:
        return f"现在本地时间是 {stamp}"
    if "星期" in what or "周" in what:
        weekday = "一二三四五六日"[datetime.now().weekday()]
        return f"现在本地时间是 {stamp}，星期{weekday}"
    return f"现在本地时间是 {stamp}"


@define_plugin(
    "clock",
    inject=("tools", "prompt"),
    provides=("clock",),
    description="把当前时间告诉模型：一个 now 工具 + 一句提示词",
    version="0.1.0",
)
def create(ctx: Context, config: dict[str, Any] | None = None) -> None:
    """装载：一个服务、一个工具、一段提示词。三笔都走 effect。"""
    settings = dict(config or {})
    unknown = set(settings) - KNOWN_CONFIG
    if unknown:
        raise PluginError(
            f"clock 配置里有不认识的键：{sorted(unknown)}；可用：{sorted(KNOWN_CONFIG)}"
        )

    clock = Clock(zone=str(settings.get("zone") or ""))
    hint = str(settings.get("hint") or "需要知道当前时间时，调用 now 工具，不要凭记忆推测。")

    ctx.provide("clock", clock)
    ctx.effect(
        lambda _ctx: ctx.service("tools").register(
            tool_from_function(now, labels=frozenset({LABEL_READ}))
        ),
        label="clock:now-tool",
    )
    # 提示词里说一句，模型才知道有这个工具。order=45 挨在工具清单（40）后面。
    ctx.effect(
        lambda _ctx: ctx.service("prompt").section(
            "clock", static(hint), order=45, title="时间"
        ),
        label="clock:prompt-section",
    )


PLUGIN = create
"""entry point 指向的对象：`clock = "dugentx_plugin_clock:PLUGIN"`。

它和 `create` 是同一个东西——取个显式名字是为了让 `pyproject.toml` 里那行
读起来没有歧义：指向的是**插件工厂**，而不是这个模块里随便哪个新建的函数。
"""
