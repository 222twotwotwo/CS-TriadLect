"""self_extension 插件 —— 让 agent 在会话中途给自己装能力。

这是整个项目里最「动态」的一块，也是最能说明「为什么每一笔注册都要
返回 disposer」的地方。

dsh 把这件事叫 self-modification：agent 能查看自己装了哪些插件，
并能挂载新的插件。它有三个前提条件，缺一个都做不干净：

1. **服务是共享的**——新挂上来的插件 `ctx.provide("weather", ...)` 之后，
   agent 下一次 `visible_tools()` 就会多出那个工具，不需要重启、不需要重载。
2. **装载返回 disposer**——拔掉的时候，插件注册过的一切（服务、监听器、
   工具、提示词段落）一起消失。做不到这一点，动态挂载就只是「装得上，
   拔不掉」，跑几个回合以后进程里全是幽灵。
3. **每一步的上下文都从日志投影**——挂载与卸载本身也要进日志，
   否则恢复会话之后，「它当时为什么多了这个工具」就成了无头案。

`manage_plugin` 的标签是 `dangerous`：默认策略会拦下它。
这不是保守，是这门课反复讲的那条——**能改自己工具箱的动作，
必须在代码侧有人点头**。要放开就在配置里把 dangerous 改成 auto，
但那个决定写得出来，看得见，也可以被审计。
"""

from __future__ import annotations

from typing import Any

from dugentx.kernel.context import Context
from dugentx.kernel.errors import PluginError, ToolError
from dugentx.kernel.loader import resolve_factory
from dugentx.kernel.plugin import Plugin, define_plugin
from dugentx.seams.agent import Agent
from dugentx.seams.tools import LABEL_DANGEROUS, tool_from_function


class DynamicPlugins:
    """`ctx.dynamicPlugins` —— 运行期挂上来的插件名单。"""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx
        self._mounted: dict[str, dict[str, Any]] = {}

    @property
    def mounted(self) -> dict[str, dict[str, Any]]:
        return dict(self._mounted)

    async def mount(
        self,
        target: Context,
        module_path: str,
        *,
        plugin_id: str = "",
        config: dict[str, Any] | None = None,
        inject: tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        # 默认 id 取模块路径的最后一段。注意要先切掉 `:属性` 那一半——
        # 解析目标支持 `模块` 和 `模块:属性` 两种写法，id 也该认得两种。
        pid = plugin_id or module_path.replace(":", ".").rsplit(".", 1)[-1]
        if pid in self._mounted:
            raise ToolError(f"插件 {pid!r} 已经挂着了；先 unmount 再挂，或者换一个 plugin_id")

        factory = resolve_factory(module_path)
        plugin: Plugin = factory(dict(config or {}))
        if not isinstance(plugin, Plugin):
            raise ToolError(
                f"{module_path!r} 的工厂返回了 {type(plugin).__name__}，不是 Plugin"
            )
        if inject is not None:
            plugin.inject = inject

        # 挂到传进来的那个上下文上——正常情况下是 agent 自己的子上下文。
        # 挂哪儿决定了拔的时候会连带撤销什么，所以这个参数不能省。
        dispose = await target.mount(plugin)
        record = {
            "id": pid,
            "module": module_path,
            "name": plugin.name,
            "provides": list(plugin.provides),
            "config": dict(config or {}),
            "dispose": dispose,
        }
        self._mounted[pid] = record
        return record

    def unmount(self, plugin_id: str) -> dict[str, Any]:
        record = self._mounted.pop(plugin_id, None)
        if record is None:
            raise ToolError(
                f"没有挂着叫 {plugin_id!r} 的插件；当前挂着：{sorted(self._mounted)}"
            )
        record["dispose"]()
        return record

    def unmount_all(self) -> list[str]:
        """卸载全部。agent 子上下文关闭时会被叫到。"""
        names = list(self._mounted)
        for name in names:
            self._mounted[name]["dispose"]()
        self._mounted.clear()
        return names


def _runtime_view(ctx: Context, agent: Agent | None) -> str:
    """看一眼自己现在是什么状态。"""
    services = sorted(ctx.services())
    tools = ctx.service("tools").names() if ctx.has("tools") else []
    lines = [
        f"服务（{len(services)}）：" + "、".join(services),
        f"工具（{len(tools)}）：" + "、".join(tools),
    ]
    if agent is not None:
        allowed = [t["function"]["name"] for t in agent.visible_tools()]
        lines.append(f"这个 agent 看得见的工具（{len(allowed)}）：" + "、".join(allowed))
        lines.append(
            f"模型：{agent.config.model or '（用默认）'}；最大步数：{agent.config.max_steps}"
        )
    dynamic = ctx.get("dynamicPlugins")
    if isinstance(dynamic, DynamicPlugins):
        lines.append(
            "运行期挂上的插件："
            + ("、".join(dynamic.mounted) if dynamic.mounted else "（无）")
        )
    return "\n".join(lines)


async def manage_plugin(
    ctx: Context,
    action: str,
    module: str = "",
    plugin_id: str = "",
    config: dict[str, Any] | None = None,
) -> str:
    """在会话中途挂载、卸载或查看自己的插件。

    Args:
        action: mount / unmount / list
        module: action=mount 时，插件模块路径，例如 dugentx.plugins.shell
        plugin_id: 给这个插件起个名字，卸载时用它；省略就用模块名末段
        config: 传给插件工厂的配置
    """
    dynamic: DynamicPlugins = ctx.service("dynamicPlugins")
    agent: Agent | None = ctx.get("agent")

    if action == "list":
        return _runtime_view(ctx, agent)

    if action == "mount":
        if not module:
            raise ToolError("mount 需要 module 参数，例如 dugentx.plugins.skill")
        target = getattr(agent, "ctx", None) or ctx
        record = await dynamic.mount(
            target, module, plugin_id=plugin_id, config=config or {}
        )
        session = ctx.get("session")
        if session is not None:
            session.append(
                "plugin/mounted",
                id=record["id"],
                module=record["module"],
                name=record["name"],
                provides=record["provides"],
            )
        ctx.events.emit("plugin/mounted", record["id"], record["module"])
        provides = "、".join(record["provides"]) or "（没有新服务，只是注册了别的东西）"
        return f"已挂上 {record['id']}（{record['name']}），它提供：{provides}"

    if action == "unmount":
        if not plugin_id:
            raise ToolError("unmount 需要 plugin_id")
        record = dynamic.unmount(plugin_id)
        session = ctx.get("session")
        if session is not None:
            session.append("plugin/unmounted", id=record["id"], name=record["name"])
        ctx.events.emit("plugin/unmounted", record["id"])
        return f"已拔掉 {record['id']}；它注册过的东西全部撤销了"

    raise ToolError(f"不认识的 action：{action!r}；只能是 mount / unmount / list")


@define_plugin(
    "selfExtension",
    inject=("tools",),
    provides=("dynamicPlugins",),
    description="运行期挂载/卸载插件的开关，附一个 manage_plugin 工具",
)
def create(ctx: Context, config: dict[str, Any] | None = None) -> None:
    """装载动态插件能力，并注册 `manage_plugin` 工具。"""
    config = config or {}
    dynamic = DynamicPlugins(ctx)
    ctx.provide("dynamicPlugins", dynamic)

    # 走 ctx.effect 而不是直接调用 register：注册必须交给作用域，
    # 否则这个插件被拔掉时工具会留在表里——一个幽灵工具，
    # 模型还看得见它，但背后已经没有人了。
    ctx.effect(
        lambda inner: inner.service("tools").register(
            tool_from_function(
                manage_plugin,
                name=str(config.get("tool_name", "manage_plugin")),
                labels=frozenset({LABEL_DANGEROUS}),
            )
        ),
        label="register:manage_plugin",
    )

    # 这个插件自己被拔掉时，顺手把它挂上来的一起拔掉。
    # 少了这一步，动态挂载就会留下一层拔不干净的残留——
    # 「每一笔注册都要能被撤销」这条规则，对动态机制自己同样成立。
    ctx.effect(lambda _ctx: dynamic.unmount_all, label="dynamicPlugins:unmount-all")


def plugin_error_to_tool_error(exc: PluginError) -> ToolError:
    """装载失败要变成模型看得懂的一句话，而不是一个栈。"""
    return ToolError(str(exc))
