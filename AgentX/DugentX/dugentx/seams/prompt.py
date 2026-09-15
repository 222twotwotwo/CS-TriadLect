"""prompt 缝 —— 系统提示词是拼出来的，不是一个字符串常量。

一条经验：**系统提示词会长成整个产品**。今天三行，半年后三百行，中间夹着
工具说明、项目规则、当前时间、用户偏好、技能目录……如果它是一个常量，
改动就变成所有人抢同一个文件。

所以这里把它拆成**段落**：谁都可以注册一段，声明自己的顺序和适用条件。
工具清单由 tools 缝输出、技能目录由 skill 缝输出、项目规则由调用方注入，
拼装顺序集中在一个地方（`assemble`），谁也不用知道别人写了什么。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from dugentx.kernel.context import Context
from dugentx.kernel.effect import Disposer

Render = Callable[[Context], str]


@dataclass(slots=True)
class PromptSection:
    """系统提示词里的一段。"""

    name: str
    render: Render
    order: int = 100
    title: str = ""


class PromptRegistry:
    """`ctx.prompt` —— 段落注册表。"""

    def __init__(self) -> None:
        self._sections: dict[str, PromptSection] = {}

    def section(
        self,
        name: str,
        render: Render,
        *,
        order: int = 100,
        title: str = "",
    ) -> Disposer:
        if name in self._sections:
            raise ValueError(f"提示词段落 {name!r} 已经注册过了")
        section = PromptSection(name=name, render=render, order=order, title=title)
        self._sections[name] = section

        def dispose() -> None:
            if self._sections.get(name) is section:
                del self._sections[name]

        return dispose

    def assemble(self, ctx: Context) -> str:
        """按 order 拼装。空段落会被跳过，不会留下一堆空行。"""
        ordered: list[PromptSection] = sorted(
            self._sections.values(), key=lambda s: (s.order, s.name)
        )
        blocks: list[str] = []
        for section in ordered:
            text = section.render(ctx).strip()
            if text:
                blocks.append(text)
        return "\n\n".join(blocks)

    def names(self) -> list[str]:
        return sorted(self._sections)


def static(text: str) -> Render:
    """固定的段落。"""

    def render(_ctx: Context) -> str:
        return text

    return render


def from_config(key: str, default: str = "") -> Render:
    """从某个服务的配置里取一段文字。"""

    def render(ctx: Context) -> str:
        agent = ctx.get("agent")
        config: dict[str, Any] = getattr(agent, "config", {}).prompt_extras if agent else {}
        return str(config.get(key, default))

    return render
