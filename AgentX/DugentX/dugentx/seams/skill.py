"""skill 缝 —— 按需加载的技能。

技能和工具的区别值得说清楚，因为它决定了你把东西写在哪：

- **工具**是模型可以**做**的事，schema 每次都进请求（占上下文，必须短）。
- **技能**是一段**写给模型看的说明**——怎么做某件事、有哪些坑。
  它只描述，不执行。全部塞进系统提示词会撑爆上下文，
  所以做成「目录常驻、正文按需」。

这叫渐进披露：系统提示词里只放一行「有哪些技能可用」，
模型觉得需要时再把它拉进来。于是你可以挂五十个技能，
而平时的上下文成本接近零。

技能正文用 Markdown 写，落成文件，改技能不用改代码——
这也是 dsh 把技能做成一个缝的原因。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(slots=True)
class SkillInfo:
    """技能目录里的一条。"""

    name: str
    description: str
    path: str = ""
    when_to_use: str = ""
    tokens: int = 0

    def render(self) -> str:
        line = f"- {self.name}：{self.description}"
        if self.when_to_use:
            line += f"（什么时候用：{self.when_to_use}）"
        return line


@runtime_checkable
class SkillProvider(Protocol):
    """`ctx.skills`。"""

    async def catalog(self) -> list[SkillInfo]: ...

    async def load(self, name: str) -> str: ...
