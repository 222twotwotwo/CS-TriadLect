"""prompt 插件 —— 系统提示词是拼出来的，不是一个字符串常量。

提示词的宿命是长大。今天五行，半年后五十行，中间挤着身份说明、工作目录、
工具清单、技能目录、项目规矩、当前时间……如果它是一个常量，改它的人就会
排队抢同一个文件；如果它是一堆 if，那它迟早会长成没人敢动的怪物。

所以这里只做一件事：**往注册表里放段落**，每段声明自己的顺序，内容自己算。
拼装由 `PromptRegistry.assemble(ctx)` 负责，采样时机由循环决定
（`agent_loop_basic` 每一步都会重新拼一次，文本变了就作为一条
`system/message` 记进日志——于是「提示词什么时候变过」是可查的）。

两个段落的内容来自别的缝：技能目录来自 `skills`（可选缝，没有就整段消失），
工具清单来自 `tools`。这就是分段的价值：工具作者不需要知道提示词长什么样，
他只要注册工具；技能作者一样。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from dugentx.kernel.context import Context
from dugentx.kernel.plugin import define_plugin
from dugentx.seams.prompt import PromptRegistry, Render, static

DEFAULT_PERSONA = """你在一个真实的工作目录里干活：用户把任务交给你，你负责把它做完。

你只能**请求**调用工具——工具由 harness 执行，执行结果会作为一条消息回到你面前。
换句话说，工具没有被真正跑过之前，那个结论不属于你。

动手改之前先看清楚：把要改的文件读一遍，确认路径存在，确认你要改的就是那一处。
没读过的东西不要改，猜出来的路径不要写。

工具报错是信息而不是失败：按报错的内容调整参数再来一次，但也别把同一件事原样重复第三遍。

任务卡住时（缺信息、缺权限、工具反复失败、需求本身矛盾）就停下来直说卡在哪里，
不要编一个看起来合理的结论把缺口盖过去。

回答尽量短：做了什么、验证了什么、还剩什么没做。已经贴过的内容不要再复述一遍。"""
"""默认的身份与工作方式。

放在代码里而不是只放配置里，是因为它同时是一份「这家 harness 怎么看待模型」的声明：
工具由谁执行、什么时候该停下来问。想改的部署在配置里写 `persona:` 覆盖即可。
"""


def _one_line(value: str, limit: int = 110) -> str:
    body = " ".join((value or "").split())
    return body[: limit - 1] + "…" if len(body) > limit else body


def tools_renderer(ctx: Context) -> str:
    """工具清单：一行一个，从注册表**现取**。

    这里没有缓存，和技能相反。理由：`ToolRegistry.all()` 是同步的，渲染时
    现取没有代价，而且运行期动态挂载的工具（`selfExtension`）会立刻出现在
    清单里——提示词一变，循环就会在日志里记下这条 `system/message`，
    于是「模型从哪一步开始看得见这个工具」是可回放的。
    """
    registry = ctx.get("tools")
    if registry is None:
        return ""
    lines = [f"- {tool.name}：{_one_line(tool.description)}" for tool in registry.all()]
    if not lines:
        return ""
    return "你可以申请调用的工具（执行权在 harness 手里）：\n" + "\n".join(lines)


def workspace_renderer(fallback: Any) -> Render:
    """工作目录。有 `fs` 服务就以它的根为准，否则用配置里的值。"""

    def render(ctx: Context) -> str:
        fs = ctx.get("fs")
        root = getattr(fs, "root", None) if fs is not None else None
        where = Path(root) if root is not None else Path(str(fallback or Path.cwd()))
        return (
            f"工作目录：{where}\n"
            "所有路径都相对于它：工具参数里的路径、命令里的路径都是如此。"
            "不要写绝对路径，也不要假设这个目录之外的东西可以随便动。"
        )

    return render


def skills_renderer(lines: list[str]) -> Render:
    """技能目录：**在装载时**读一次，之后只渲染缓存。

    为什么要缓存：`render` 是同步的（`PromptRegistry.assemble` 是同步的），
    而 `SkillProvider.catalog()` 是异步的——同步函数里 await 不了。折中是
    在装载时 await 一次（插件本体是 async 的，这一步允许），把结果存下来。

    代价说清楚：**运行期新增的技能不会出现在这段文字里**，要等这个插件被卸载重挂。
    按需加载的技能正文不受影响（`skill` 工具照常能读），只是目录里暂时看不到它。
    对「目录常驻、正文按需」的设计来说，这个代价可以接受；
    真需要动态目录时，正确做法是重新挂载 prompt 插件，而不是在这里塞一个定时刷新。
    """

    def render(_ctx: Context) -> str:
        if not lines:
            return ""
        return "可以按需加载的技能（说一声就拉进上下文）：\n" + "\n".join(lines)

    return render


async def _read_skill_lines(ctx: Context) -> list[str]:
    """读技能目录；没有 `skills` 服务就安静地返回空。

    skill 是**可选缝**：一个不装技能的部署不该因为少了一行配置就在提示词里
    出现「（技能服务缺失）」这种噪音，更不该装不起来。
    """
    provider = ctx.get("skills")
    if provider is None:
        return []
    catalog = await provider.catalog()
    return [info.render() for info in catalog]


@define_plugin(
    "prompt",
    inject=("tools",),
    provides=("prompt",),
    description="系统提示词段落注册表：身份、工作目录、技能目录、工具清单、额外规则",
)
async def create(ctx: Context, config: dict[str, Any] | None = None) -> None:
    """注册五段提示词。

    顺序即结构：`persona` 说「你是谁、怎么干活」，`workspace` 说「在哪干」，
    `skills`/`tools` 说「手上有什么」，`extras` 留给部署方塞自己的规矩（放在最后，
    离用户最近、也最容易被读到）。
    """
    settings = dict(config or {})
    registry = PromptRegistry()
    skill_lines = await _read_skill_lines(ctx)

    persona = str(settings.get("persona") or DEFAULT_PERSONA)
    sections: list[tuple[str, Render, int, str]] = [
        ("persona", static(persona), 10, "身份与工作方式"),
        ("workspace", workspace_renderer(settings.get("workspace")), 20, "工作目录"),
        ("skills", skills_renderer(skill_lines), 30, "技能目录"),
        ("tools", tools_renderer, 40, "工具清单"),
        ("extras", static(str(settings.get("extras") or "")), 90, "部署方自己的规则"),
    ]
    for name, render, order, title in sections:
        ctx.effect(
            _register_section(registry, name, render, order, title), label=f"prompt:{name}"
        )

    ctx.provide("prompt", registry)


def _register_section(
    registry: PromptRegistry, name: str, render: Render, order: int, title: str
) -> Callable[[Context], Any]:
    """包一层，让每段的注册都成为一笔可以撤销的 effect。"""

    def register(_ctx: Context) -> Any:
        return registry.section(name, render, order=order, title=title)

    return register
