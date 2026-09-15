"""插件清单 —— 插件包对外的「一张脸」。

一个插件如果只能是「配置里的一串 `包.模块:属性`」，那么它在被装载之前
**什么都问不出来**：要它要哪些服务？提供什么？对不对得上这个版本的 harness？
唯一的办法是把模块 import 进来、把工厂调起来——也就是执行它的代码。
这让「先看看装了些什么、再决定装哪些」变成一件做不到的事。

清单解决的就是这件事：它是**声明**，不是执行结果。装载器、`dugentx plugins
--available`、以及任何想在不装载的前提下检查组合的东西，都读它。

清单从哪来？从 `define_plugin` 装饰过的那把工厂上读（见 `plugin.py`）——
单一来源，不会和代码里的 `inject` / `provides` 分叉。一个插件包如果还想声明
版本、来源这些代码里没有的东西，在自己的模块里写 `MANIFEST = PluginManifest(...)`
即可，注册表会认。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

PLUGIN_API_VERSION = "1"
"""插件接口的版本。

插件包在清单里声明它按哪个版本写的；harness 只装载自己认识的版本。
不加这个字段的话，「接口变了」这件事只能靠插件在自己的代码里
因为 `AttributeError` 而崩掉来发现——那时错误信息会指向插件的某一行，
而不是「你的插件是给 v0 写的，这里是 v1」。
"""


@dataclass(frozen=True, slots=True)
class PluginManifest:
    """一个插件包的自我声明。"""

    name: str
    version: str = "0.0.0"
    """插件自己的版本。插件作者负责，harness 只负责显示。"""

    api_version: str = PLUGIN_API_VERSION
    """它按哪一版插件接口写的。对不上就不装载，并在报错里说清两边各是多少。"""

    description: str = ""
    inject: tuple[str, ...] = ()
    provides: tuple[str, ...] = ()
    target: str = ""
    """`包.模块:属性` —— 装载器照着它去拿工厂。"""

    distribution: str = ""
    """它来自哪个已安装的发行包。内置插件留空——它们不在任何一个发行包里。"""

    @property
    def compatible(self) -> bool:
        """这份清单和当前 harness 的插件接口对得上吗。"""
        return self.api_version == PLUGIN_API_VERSION

    def describe(self) -> str:
        """一行给人看的摘要，`dugentx plugins --available` 用它。"""
        bits = [f"{self.name} {self.version}"]
        if self.inject:
            bits.append("需要[" + ", ".join(self.inject) + "]")
        if self.provides:
            bits.append("提供[" + ", ".join(self.provides) + "]")
        if not self.compatible:
            bits.append(f"⚠ 接口版本 {self.api_version}，本机是 {PLUGIN_API_VERSION}")
        return "  ".join(bits)


def manifest_of(candidate: Any) -> PluginManifest | None:
    """从一把插件工厂上取出清单；取不到就返回 `None`。

    `None` 是正常结果，不是错误：清单是**新**接口的一部分，老插件没有它，
    照样能被 `包.模块:属性` 装载。这条兼容路径要一直在——否则每加一个
    元数据字段，都等于把所有已存在的插件判了死刑。
    """
    found = getattr(candidate, "__dugentx_manifest__", None)
    if isinstance(found, PluginManifest):
        return found
    return None
