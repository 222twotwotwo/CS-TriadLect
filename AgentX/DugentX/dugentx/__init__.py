"""DugentX —— 一个可拔插的 Agent harness。

三句话说明这份代码的立场：

1. **LLM 层不自造。** 所有模型调用都走 `any-llm`，DugentX 里没有一处手写的
   HTTP 请求。换 provider 不改一行 harness 代码。
2. **其他全部自造。** Agent 循环、工具管道、会话日志、权限、上下文压缩、
   子 Agent、技能加载——这些是一家 harness 真正要做的事，全部在 `dugentx/` 里。
3. **没有特殊的内核。** 模型适配器、工具注册表、会话日志、循环本身都是插件，
   都可以从配置里换掉。要扩展就挂一个新插件，不要去改老代码。

架构参考 dsh（DeepSeek Harness）的可拔插模型，用 Python 重写成一个能读完的体量。
"""

from dugentx.kernel.context import Context
from dugentx.kernel.effect import EffectScope
from dugentx.kernel.errors import (
    DuGentXError,
    PluginError,
    ServiceNotFound,
    ToolError,
)
from dugentx.kernel.events import EventBus
from dugentx.kernel.loader import Composition, load_composition
from dugentx.kernel.plugin import Plugin, define_plugin
from dugentx.runtime import AgentRuntime

__all__ = [
    "AgentRuntime",
    "Composition",
    "Context",
    "DuGentXError",
    "EffectScope",
    "EventBus",
    "Plugin",
    "PluginError",
    "ServiceNotFound",
    "ToolError",
    "define_plugin",
    "load_composition",
]

__version__ = "0.1.0"
