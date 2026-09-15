"""DugentX 的异常。

规则只有一条：**配置错了要在装载时就大声失败**，不要留到运行期静默降级。
"""

from __future__ import annotations


class DuGentXError(Exception):
    """所有 DugentX 异常的基类。"""


class PluginError(DuGentXError):
    """插件装载、依赖解析或生命周期出错。"""


class ServiceNotFound(DuGentXError):
    """想用一个当前上下文里根本没有的服务。

    这条会被大声抛出来，而不是回退到一个默认实现——回退会让
    「我明明配了那个插件，为什么没生效」变成一个查不出来的问题。
    """

    def __init__(self, key: str, available: list[str]) -> None:
        self.key = key
        self.available = available
        super().__init__(
            f"上下文里没有服务 {key!r}；当前可用：{sorted(available) or '（空）'}"
        )


class ToolError(DuGentXError):
    """工具执行失败。

    这些异常会被工具管道捕获并作为一条 tool 消息回传给模型，
    让模型自己想办法——而不是把整个会话打断。
    """


class ToolBlocked(ToolError):
    """工具被策略拦下来了（权限、白名单、参数校验）。

    与 `ToolError` 的区别是：这不是工具坏了，是**有人不允许它跑**。
    """


class PermissionDenied(ToolBlocked):
    """用户或策略明确拒绝了这次调用。"""
