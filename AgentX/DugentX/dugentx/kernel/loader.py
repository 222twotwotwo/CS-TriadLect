"""组合：把一份 YAML 变成一棵装好的插件树。

dsh 用 `profile → bundle → patch` 三层叠出一份 `cordis.yml`。
DugentX 保留「层」这个想法，但只留一层：一份配置列表 + 一组按 id 的覆盖。
对示例项目来说，这已经能表达「换一个 adapter」「关掉一个工具」
「给某一行换配置」这三件真正会发生的事。

装载分四步，每一步失败都**在装载时**报错，不留到运行期：

1. 读 YAML，按 id 应用覆盖。
2. 解析每一行的 `plugin` 指向哪个工厂。
3. 校验：某一行 `inject` 的服务，必须有人 `provides`——否则这张图是坏的。
4. 按 `inject → provides` 拓扑排序（手写启动顺序是这里明确要避免的东西）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from dugentx.kernel.errors import PluginError
from dugentx.kernel.plugin import Plugin

# 解析规则住在 `kernel/registry.py` —— 那是 meta 接口的所在，它同时管
# 「已安装的插件包」（走 entry point）和「仓库里的模块」（走 import）。
# 这里保留同名入口，是为了不让 `from dugentx.kernel.loader import resolve_factory`
# 的那些调用方跟着搬家。
from dugentx.kernel.registry import resolve_factory as resolve_factory


@dataclass(slots=True)
class PluginRow:
    """配置里的一行。"""

    id: str
    plugin: str
    config: dict[str, Any] = field(default_factory=dict)
    disabled: bool = False
    inject: tuple[str, ...] | None = None
    """覆盖插件自带的 inject。运行期动态挂载时会用到。"""

    @classmethod
    def from_mapping(cls, raw: dict[str, Any], *, where: str) -> PluginRow:
        if "id" not in raw or "plugin" not in raw:
            raise PluginError(f"{where} 的每一行都必须有 id 和 plugin；收到：{raw!r}")
        inject = raw.get("inject")
        return cls(
            id=str(raw["id"]),
            plugin=str(raw["plugin"]),
            config=dict(raw.get("config") or {}),
            disabled=bool(raw.get("disabled", False)),
            inject=tuple(inject) if inject is not None else None,
        )


@dataclass(slots=True)
class Composition:
    """一份排好序、已经实例化的插件列表。"""

    rows: list[PluginRow]
    plugins: list[Plugin]
    source: str = ""

    def describe(self) -> str:
        lines = []
        for index, (row, plugin) in enumerate(zip(self.rows, self.plugins, strict=True), start=1):
            inject = ", ".join(plugin.inject) or "—"
            provides = ", ".join(plugin.provides) or "—"
            lines.append(f"  {index:>2}. {row.id:<16} 装 {plugin.name:<16} 需要[{inject}]")
            lines.append(f"      {row.plugin:<42} 提供[{provides}]")
        return "\n".join(lines)


def apply_patches(
    rows: list[PluginRow], patches: list[dict[str, Any]], *, where: str
) -> list[PluginRow]:
    """按 id 覆盖配置；id 不存在就插入一行。

    覆盖是**整行 config 替换**，不是深合并——深合并会让删掉一个键
    变成一件说不清楚的事。
    """
    out = list(rows)
    index = {row.id: i for i, row in enumerate(out)}
    for patch in patches:
        if "id" not in patch:
            raise PluginError(f"{where} 里的覆盖项缺少 id：{patch!r}")
        target = str(patch["id"])
        if target in index:
            current = out[index[target]]
            out[index[target]] = PluginRow(
                id=current.id,
                plugin=str(patch.get("plugin", current.plugin)),
                config=dict(patch["config"]) if "config" in patch else current.config,
                disabled=bool(patch.get("disabled", current.disabled)),
                inject=(
                    tuple(patch["inject"])
                    if "inject" in patch
                    else current.inject
                ),
            )
        else:
            out.append(PluginRow.from_mapping(patch, where=f"{where} 的覆盖项"))
            index[target] = len(out) - 1
    return out


def order_plugins(rows: list[PluginRow], plugins: list[Plugin]) -> list[int]:
    """按 `inject → provides` 拓扑排序，返回下标的顺序。"""
    provider_of: dict[str, int] = {}
    for i, plugin in enumerate(plugins):
        for key in plugin.provides:
            if key in provider_of:
                raise PluginError(
                    f"服务 {key!r} 被两个插件同时声明提供："
                    f"{rows[provider_of[key]].id!r} 和 {rows[i].id!r}。"
                    f"同一个键只能有一个提供者。"
                )
            provider_of[key] = i

    for i, plugin in enumerate(plugins):
        for key in plugin.inject:
            if key not in provider_of:
                raise PluginError(
                    f"插件 {rows[i].id!r} 需要服务 {key!r}，"
                    f"但配置里没有任何一行声明提供它。"
                    f"当前能提供的是：{sorted(provider_of) or '（空）'}"
                )

    order: list[int] = []
    state: dict[int, int] = {}  # 0 未访问 / 1 访问中 / 2 已完成

    def visit(i: int, trail: list[int]) -> None:
        mark = state.get(i)
        if mark == 2:
            return
        if mark == 1:
            cycle = " → ".join(rows[j].id for j in [*trail, i])
            raise PluginError(f"插件依赖成环：{cycle}")
        state[i] = 1
        for key in plugins[i].inject:
            visit(provider_of[key], [*trail, i])
        state[i] = 2
        order.append(i)

    for i in range(len(plugins)):
        visit(i, [])
    return order


def _read_document(path: Path) -> dict[str, Any]:
    """读一份 YAML 配置，确认顶层是映射。"""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise PluginError(f"{path} 的顶层必须是一个映射，收到 {type(data).__name__}")
    return data


def _collect_rows(
    path: Path, seen: tuple[Path, ...] = ()
) -> tuple[list[PluginRow], list[dict[str, Any]]]:
    """读一份配置，连同它 `extends` 的基座一起。

    `extends` 是 dsh 的 profile / bundle / patch 三层叠法的一层化版本：
    基座的插件行先来，自己的 `plugins` 追加在后面，最后统一应用 patch。
    没有它，「换一个人机通道」就得把整份配置复制一遍——而复制出来的两份
    会各自漂移，这正是配置最容易烂掉的方式。

    只解析一层链，并且拒绝成环：配置里的环报出来是「a.yml extends b.yml
    extends a.yml」，比一个栈溢出好查得多。
    """
    resolved = path.resolve()
    if resolved in seen:
        chain = " → ".join(str(p.name) for p in (*seen, resolved))
        raise PluginError(f"配置 extends 成环：{chain}")
    if len(seen) >= 8:
        raise PluginError(f"配置 extends 太深（超过 8 层）：{path}")

    data = _read_document(path)
    base_rows: list[PluginRow] = []
    base_patches: list[dict[str, Any]] = []

    parent = data.get("extends")
    if parent:
        parent_path = (path.parent / str(parent)).resolve()
        if not parent_path.exists():
            raise PluginError(f"{path} 的 extends 指向了不存在的文件：{parent}")
        base_rows, base_patches = _collect_rows(parent_path, (*seen, resolved))

    own_rows = [
        PluginRow.from_mapping(r, where=str(path))
        for r in (data.get("plugins") or [])
        if isinstance(r, dict)
    ]
    # 自己的 patch 在基座的 patch 之后应用，所以子配置能覆盖父配置改过的东西。
    patches = [*base_patches, *[p for p in (data.get("patch") or []) if isinstance(p, dict)]]
    return [*base_rows, *own_rows], patches


def load_composition(
    path: str | Path | None = None,
    *,
    overrides: list[dict[str, Any]] | None = None,
) -> Composition:
    """读配置、覆盖、校验、排序、实例化。"""
    if path is None:
        rows: list[PluginRow] = []
        source = "（空配置）"
    else:
        rows, patches = _collect_rows(Path(path))
        source = str(path)
        rows = apply_patches(rows, patches, where=source)

    if overrides:
        rows = apply_patches(rows, overrides, where="命令行覆盖")

    rows = [r for r in rows if not r.disabled]

    plugins: list[Plugin] = []
    for row in rows:
        factory = resolve_factory(row.plugin)
        plugin = factory(row.config)
        if not isinstance(plugin, Plugin):
            raise PluginError(
                f"{row.plugin!r} 的工厂返回了 {type(plugin).__name__}，不是 Plugin；"
                f"插件模块要用 @define_plugin 装饰工厂函数"
            )
        if row.inject is not None:
            plugin.inject = row.inject
        plugins.append(plugin)

    order = order_plugins(rows, plugins)
    return Composition(
        rows=[rows[i] for i in order],
        plugins=[plugins[i] for i in order],
        source=source,
    )
