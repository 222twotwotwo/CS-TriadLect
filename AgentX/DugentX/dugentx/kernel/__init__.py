"""内核：与任何具体能力无关的那一层。

`context` / `plugin` / `effect` / `events` / `loader` 五件东西构成了
「可拔插」这件事本身。它们不知道 Agent 是什么，也不 import 任何能力实现。
"""

from dugentx.kernel.context import Context
from dugentx.kernel.effect import Disposer, EffectScope
from dugentx.kernel.events import EventBus, EventSpec
from dugentx.kernel.loader import Composition, PluginRow, load_composition
from dugentx.kernel.plugin import Plugin, define_plugin

__all__ = [
    "Composition",
    "Context",
    "Disposer",
    "EffectScope",
    "EventBus",
    "EventSpec",
    "Plugin",
    "PluginRow",
    "define_plugin",
    "load_composition",
]
