"""插件注册表 —— DugentX 的 meta 接口。

「meta」是相对于「装载」说的。装载是**执行**一个插件；注册表只回答关于插件的
问题：装了哪些、各自要求什么、是不是给这个版本的接口写的。它**不执行插件代码**
（除了 `import` 自己那个模块），所以：

- `dugentx plugins --available` 不需要 key、不需要把组合装起来就能列出来；
- 装载器在动手之前就能判断「这个插件要的服务没人提供」；
- 接口版本对不上时，报错说的是版本，而不是插件里某一行 `AttributeError`。

插件包通过**标准 entry point** 接入，组名是 `dugentx.plugins`：

```toml
[project.entry-points."dugentx.plugins"]
word-count = "dugentx_word_count:PLUGIN"
```

于是「写一个插件包」和「让 DugentX 看见它」是同一件事：
`pip install` 之后它就在注册表里了，配置里写它的名字即可。

配置里**照旧**可以写 `包.模块:属性` 指向仓库内的模块——那条路一点没变。
包是给人分发的，模块是给自己用的，两种都该有。
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from dugentx.kernel.errors import PluginError
from dugentx.kernel.manifest import PLUGIN_API_VERSION, PluginManifest, manifest_of
from dugentx.kernel.plugin import Plugin

#: 插件包注册自己用的 entry point 组名。这是**对外契约**，改了就是破坏性变更。
PLUGIN_GROUP = "dugentx.plugins"

PluginFactory = Callable[[dict[str, Any]], Plugin]


@dataclass(frozen=True, slots=True)
class PluginEntry:
    """注册表里的一条：一个已安装的插件包。

    为什么不让调用方直接拿 `importlib.metadata.EntryPoint`：那样测试就得伪造
    一个带 `.load()` / `.dist` 的库对象，而这里真正需要的信息只有三样。
    归一化成这个形状之后，测试注入几条假的就能覆盖全部分支。
    """

    name: str
    target: str
    loader: Callable[[], Any]
    distribution: str = ""


def installed_entries() -> list[PluginEntry]:
    """从已安装的发行包里读出插件包。这是唯一碰 `importlib.metadata` 的地方。"""
    from importlib.metadata import entry_points

    out: list[PluginEntry] = []
    for entry in entry_points(group=PLUGIN_GROUP):
        dist = getattr(entry, "dist", None)
        out.append(
            PluginEntry(
                name=entry.name,
                target=getattr(entry, "value", "") or "",
                loader=entry.load,
                distribution=str(getattr(dist, "name", "") or ""),
            )
        )
    return out


class PluginRegistry:
    """已安装插件包的名单。

    `source` 可以换掉——测试注入一个假的来源就能在**不安装任何东西**的前提下
    跑完发现、解析、版本校验这几条路。这也是它能被离线测试的原因。
    """

    def __init__(self, *, source: Callable[[], Iterable[PluginEntry]] | None = None) -> None:
        self._source = source or installed_entries
        self._entries: dict[str, PluginEntry] | None = None

    # ------------------------------------------------------------------ 名单

    def _load(self) -> dict[str, PluginEntry]:
        if self._entries is None:
            found: dict[str, PluginEntry] = {}
            for entry in self._source():
                found[entry.name] = entry
            self._entries = found
        return self._entries

    def names(self) -> list[str]:
        return sorted(self._load())

    def has(self, name: str) -> bool:
        return name in self._load()

    def entry(self, name: str) -> PluginEntry:
        try:
            return self._load()[name]
        except KeyError:
            known = self.names()
            hint = (
                "已安装的插件包有：" + "、".join(known)
                if known
                else "当前一个插件包都没装。用包名前先 pip install 它，"
                "或者照旧写 `包.模块:属性` 指向仓库里的模块。"
            )
            raise PluginError(f"没有叫 {name!r} 的插件包。{hint}") from None

    # ------------------------------------------------------------------ 清单

    def manifest(self, name: str) -> PluginManifest:
        """读一个插件包的清单。**不装载它**，只是 import 它那个模块。"""
        entry = self.entry(name)
        factory = self._factory_of(entry)
        declared = manifest_of(factory)
        if declared is None:
            # 老插件没有清单：照样能用，但它是「无法自我介绍」的，
            # 我们只能把从 entry point 上知道的那些填进去。
            return PluginManifest(
                name=entry.name,
                target=entry.target,
                distribution=entry.distribution,
                description="（这个插件没有声明清单）",
            )
        return PluginManifest(
            name=declared.name,
            version=declared.version,
            api_version=declared.api_version,
            description=declared.description,
            inject=declared.inject,
            provides=declared.provides,
            target=entry.target or declared.target,
            distribution=entry.distribution or declared.distribution,
        )

    def manifests(self) -> list[PluginManifest]:
        """全部插件包的清单，按名字排序。这是 `dugentx plugins --available` 的来源。"""
        return [self.manifest(name) for name in self.names()]

    # ------------------------------------------------------------------ 解析

    def factory(self, name: str) -> PluginFactory:
        """拿到插件包的工厂，并在这里做接口版本校验。"""
        entry = self.entry(name)
        manifest = self.manifest(name)
        if not manifest.compatible:
            raise PluginError(
                f"插件包 {name!r} 声明的是插件接口 v{manifest.api_version}，"
                f"本机这个 harness 是 v{PLUGIN_API_VERSION}。"
                f"要么升级这个插件包，要么退回对应版本的 DugentX。"
            )
        return self._factory_of(entry)

    def _factory_of(self, entry: PluginEntry) -> PluginFactory:
        try:
            candidate = entry.loader()
        except ImportError as exc:
            raise PluginError(
                f"插件包 {entry.name!r} 指向的模块 import 失败：{exc}"
            ) from exc
        if not callable(candidate):
            raise PluginError(
                f"插件包 {entry.name!r} 的入口 {entry.target!r} 不是一个可调用的插件工厂。"
                f"它应该指向一个被 `define_plugin` 装饰过的函数。"
            )
        return candidate  # type: ignore[no-any-return]


def import_factory(target: str) -> PluginFactory:
    """把 `包.模块` 或 `包.模块:属性` 解析成插件工厂（不经过注册表的那条路）。"""
    module_path, _, attr = target.partition(":")
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        raise PluginError(f"找不到插件模块 {module_path!r}：{exc}") from exc

    if attr:
        candidate = getattr(module, attr, None)
        if candidate is None:
            raise PluginError(f"{module_path!r} 里没有 {attr!r}")
        return candidate  # type: ignore[no-any-return]

    candidate = getattr(module, "create", None)
    if candidate is None:
        raise PluginError(
            f"插件模块 {module_path!r} 既没有 `create`，也没在配置里指出 :属性 名"
        )
    return candidate  # type: ignore[no-any-return]


def resolve_factory(
    target: str, *, registry: PluginRegistry | None = None
) -> PluginFactory:
    """统一入口：**先当插件包名查，查不到再当模块路径 import**。

    顺序不能反过来按「有没有点」判断：`module:attr` 里的 module 完全可以是个
    不带点的顶层模块名（测试和临时插件到处都是 `my_module:create` 这种写法），
    用「有没有点」当判据会把它误判成包名。而一个已安装的插件包名，
    在注册表里是查得到的——这是比字符串形状更可靠的依据。
    """
    reg = registry or installed_registry()
    if reg.has(target):
        return reg.factory(target)
    if "." in target or ":" in target:
        return import_factory(target)
    known = reg.names()
    hint = (
        "已安装的插件包有：" + "、".join(known)
        if known
        else "当前一个插件包都没装。"
    )
    raise PluginError(
        f"{target!r} 既不是已安装的插件包，也不像模块路径"
        f"（模块路径至少要有一个 `.` 或一个 `:`）。{hint}"
    )


_CACHED: PluginRegistry | None = None


def installed_registry() -> PluginRegistry:
    """进程级的注册表。发现一次就够——entry point 在一次运行里不会变。"""
    global _CACHED
    if _CACHED is None:
        _CACHED = PluginRegistry()
    return _CACHED
