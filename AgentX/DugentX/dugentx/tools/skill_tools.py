"""use_skill 工具 —— 把一段技能正文拉进上下文。

技能是渐进披露的：系统提示词里只有每个技能的一行摘要（名字 + 用途），
正文躺在文件里。所以模型觉得需要时，必须由它自己把这个工具调起来——
这就是「目录常驻、正文按需」落地的地方。

加载动作会**记进会话日志**（`skill/loaded`），这是刻意的选择：技能正文
改变了模型的行为，那么「这一轮它看过哪些说明」就应该是可回查的事实，
而不是只留在上下文里的一段文本。同一件事也顺便解释了为什么这个工具是
`read` 标签：它只读，不改任何东西。
"""

from __future__ import annotations

from typing import Any

from dugentx.kernel.context import Context
from dugentx.kernel.effect import Disposer
from dugentx.kernel.errors import PluginError
from dugentx.kernel.plugin import define_plugin
from dugentx.seams.compaction import estimate_tokens
from dugentx.seams.tools import LABEL_READ, tool_from_function


async def use_skill(ctx: Context, name: str) -> str:
    """读出某个技能的正文。

    技能是一段写给模型看的操作说明：某件事怎么做、有哪些坑、这个仓库
    有哪些不成文的约定。它不是可执行的能力，读进来只是多了一段说明。

    Args:
        name: 技能名，就是技能目录里那个文件夹的名字；可用技能见系统提示词里的目录
    """
    skills = ctx.service("skills")
    session = ctx.service("session")
    body = await skills.load(name)
    tokens = estimate_tokens(body)
    session.append("skill/loaded", name=name, tokens=tokens, chars=len(body))
    ctx.events.emit("skill/loaded", name, tokens)
    return body


def register(ctx: Context) -> Disposer:
    """把 `use_skill` 注册进工具表，返回撤销它的 disposer。

    返回 disposer 而不是直接注册完就算：工具插件被拔掉时（运行期动态卸载），
    模型眼前的工具列表必须跟着消失，否则它会调用一个已经不存在的服务。
    """
    return ctx.service("tools").register(
        tool_from_function(use_skill, labels=frozenset({LABEL_READ}))
    )


@define_plugin(
    "skill-tools",
    inject=("tools", "skills", "session"),
    description="use_skill：按需把技能正文读进上下文",
)
def create(ctx: Context, config: dict[str, Any]) -> None:
    """注册技能工具。

    它要 `skills`（正文从哪来）、`tools`（注册到哪去）、`session`（记一笔
    加载事件）。三个都是硬依赖：缺了任何一个，这个工具要么没东西可读，
    要么读了不留痕——那就没有装它的理由。
    """
    if config:
        raise PluginError(
            f"skill-tools 没有可配的项，却收到了 {sorted(config)}；"
            f"技能目录配在 skill 插件的 directories 上"
        )
    ctx.effect(register, label="tool:use_skill")
