"""skill 插件 —— 把几个目录挂成技能目录。

技能目录是可配置的，这一点值得强调：技能是写给人改的内容，不是代码。
一个团队可以挂自己的目录（`team/skills`）去覆盖课程自带的例子，
而装载顺序、覆盖规则、什么时候生效，全都在这个插件里说清楚——
内容不该为了改一行说明重新部署一次 harness。

目录不存在就在装载时报错。技能最差的一种失败形态是「静默地少了一条」：
模型看不到那个技能，于是它自己编一个做法，而你以为技能装上了。
"""

from __future__ import annotations

from typing import Any

from dugentx.kernel.context import Context
from dugentx.kernel.plugin import define_plugin
from dugentx.providers.skill_filesystem import FilesystemSkills


@define_plugin(
    "skill",
    provides=("skills",),
    description="文件系统上的技能目录：目录常驻、正文按需",
)
def create(ctx: Context, config: dict[str, Any]) -> None:
    """装载技能 provider。

    它不依赖任何服务：技能只读磁盘，和被装载在哪一层无关。
    这也是「技能比工具更好扩展」的一个侧面——加一个技能不需要装别的插件，
    只要把文件放进去。
    """
    ctx.provide("skills", FilesystemSkills.from_config(config, events=ctx.events))
