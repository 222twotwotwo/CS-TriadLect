"""插件包的发现与解析 —— DugentX 的 meta 接口。

「meta」是相对于「装载」说的：这一层只回答关于插件的问题（装了哪些、各自要求
什么、按哪版接口写的），**不执行插件代码**。所以这些测试全部离线、不安装任何东西——
注册表的来源是可注入的，注入几条假的就能覆盖发现、解析、版本校验每一条分支。

真正需要在真实环境里验证的部分（entry point 能不能被 importlib.metadata 读到）
由 `dugentx plugins --available` 承担，见 `examples/dugentx-plugin-clock/`。
"""

from __future__ import annotations

from typing import Any

import pytest

from dugentx.kernel.errors import PluginError
from dugentx.kernel.manifest import PLUGIN_API_VERSION, PluginManifest, manifest_of
from dugentx.kernel.plugin import Plugin, define_plugin
from dugentx.kernel.registry import PluginEntry, PluginRegistry, resolve_factory


@define_plugin(
    "fake",
    inject=("tools",),
    provides=("fake",),
    description="一个假的插件，用来测注册表",
    version="2.0",
)
def fake_plugin(ctx: Any, config: dict[str, Any] | None = None) -> None:
    """装一个假的插件。"""


def registry(*entries: PluginEntry) -> PluginRegistry:
    """一个不从环境发现、只看我们给它的那几条的注册表。"""
    return PluginRegistry(source=lambda: list(entries))


def entry(**overrides: Any) -> PluginEntry:
    base = {
        "name": "fake",
        "target": "some.module:fake_plugin",
        "loader": lambda: fake_plugin,
        "distribution": "fake-dist",
    }
    return PluginEntry(**{**base, **overrides})


# ---------------------------------------------------------------------- 清单


def test_a_factory_declares_its_manifest_without_being_called() -> None:
    """清单是**声明**：不调用工厂就能读到它要什么、给什么。"""
    manifest = manifest_of(fake_plugin)

    assert manifest is not None
    assert manifest.name == "fake"
    assert manifest.inject == ("tools",)
    assert manifest.provides == ("fake",)
    assert manifest.version == "2.0"
    assert manifest.api_version == PLUGIN_API_VERSION
    assert manifest.compatible


def test_a_plugin_without_a_manifest_still_works() -> None:
    """老插件没有清单是**正常**的，不是错误。

    否则每加一个元数据字段，就等于把所有已存在的插件判了死刑。
    """

    def plain(config: dict[str, Any] | None = None) -> Plugin:  # pragma: no cover
        raise AssertionError("这里不该被调用")

    assert manifest_of(plain) is None


# ---------------------------------------------------------------------- 发现


def test_the_registry_lists_what_is_installed_with_its_declaration() -> None:
    reg = registry(entry())

    assert reg.names() == ["fake"]
    manifest = reg.manifest("fake")
    assert manifest.inject == ("tools",)
    assert manifest.distribution == "fake-dist"
    assert manifest.target == "some.module:fake_plugin"
    assert reg.factory("fake") is fake_plugin


def test_an_unknown_package_name_lists_what_is_installed() -> None:
    with pytest.raises(PluginError) as info:
        registry(entry()).factory("nosuch")

    assert "nosuch" in str(info.value)
    assert "fake" in str(info.value)  # 报错要说清有哪些可选


def test_an_empty_registry_points_at_the_module_path_fallback() -> None:
    """一个包都没装时，报错不该只留一句「找不到」。"""
    with pytest.raises(PluginError) as info:
        registry().factory("nosuch")

    assert "模块" in str(info.value)


# ------------------------------------------------------------------ 版本校验


def test_a_plugin_written_for_another_interface_version_is_refused() -> None:
    """接口对不上时，报错说的是**版本**，而不是插件里某一行 AttributeError。"""

    def future(config: dict[str, Any] | None = None) -> Plugin:  # pragma: no cover
        raise AssertionError("不该装载")

    future.__dugentx_manifest__ = PluginManifest(name="future", api_version="99")  # type: ignore[attr-defined]
    reg = registry(entry(name="future", loader=lambda: future))

    assert not reg.manifest("future").compatible
    with pytest.raises(PluginError) as info:
        reg.factory("future")
    assert "99" in str(info.value)
    assert PLUGIN_API_VERSION in str(info.value)


# ---------------------------------------------------------------------- 解析


def test_an_installed_package_name_wins_over_a_module_path() -> None:
    """判据是「注册表里有没有」，不是「字符串里有没有点」。

    `module:attr` 里的 module 完全可以是个不带点的顶层模块名
    （`my_module:create` 这种写法到处都是），所以按形状猜一定会猜错。
    """
    reg = registry(entry(name="dugentx.plugins.shell"))

    assert resolve_factory("dugentx.plugins.shell", registry=reg) is fake_plugin


def test_a_module_path_still_resolves_when_nothing_is_registered() -> None:
    """老的写法一点没变：仓库里的模块照旧按 `包.模块` 找到。"""
    factory = resolve_factory("dugentx.plugins.tools", registry=registry())

    assert callable(factory)


def test_a_bare_name_that_is_neither_is_refused_clearly() -> None:
    with pytest.raises(PluginError) as info:
        resolve_factory("nosuchplugin", registry=registry())

    assert "nosuchplugin" in str(info.value)
