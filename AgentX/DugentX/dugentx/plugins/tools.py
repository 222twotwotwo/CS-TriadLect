"""tools 插件 —— 提供工具注册表。

它自己不带任何工具。工具包（`dugentx/tools/*.py`）在配置里各占一行，
各自声明自己需要哪些服务。这么排的原因是装载顺序：
装了 fs 才有 `ctx.fs`，有了 `ctx.fs` 才能注册 `read_file`。
把这层依赖交给配置去表达，而不是写死在一个「把所有工具都注册一遍」的函数里。
"""

from __future__ import annotations

from typing import Any

from dugentx.kernel.context import Context
from dugentx.kernel.plugin import define_plugin
from dugentx.seams.tools import ToolRegistry


@define_plugin(
    "tools",
    provides=("tools",),
    description="工具注册表 + 四段执行管道（pre-execute / execute / post-execute）",
)
def create(ctx: Context, config: dict[str, Any] | None = None) -> None:
    """装载工具注册表。它本身没有状态，也不需要配置。"""
    config = config or {}
    registry = ToolRegistry(ctx)

    if config.get("description_style") == "compact":
        # 给上下文紧张的场景留一个开关：把工具描述压到一句话。
        # 放在这里而不是让每个工具自己判断，是因为「描述要多长」
        # 是部署侧的决定，不是工具作者的决定。
        original = registry.specs

        def compact() -> list[dict[str, Any]]:
            specs = original()
            for spec in specs:
                fn = spec["function"]
                fn["description"] = fn["description"].strip().split("\n")[0][:120]
            return specs

        registry.specs = compact  # type: ignore[method-assign]

    ctx.provide("tools", registry)
