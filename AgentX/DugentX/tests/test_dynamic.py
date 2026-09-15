"""运行期动态挂载的测试。

这是整个项目的招牌功能，所以它值得一组专门的测试。这里要证明的不是
「能挂上」，而是**挂上之后真的生效、拔掉之后真的干净**：

- 挂上来的插件提供的新服务，立刻能被取到；
- 它注册的工具，立刻出现在 agent 看得见的工具列表里；
- 挂载与卸载都进会话日志，重放时说得清当时多过什么；
- 拔掉之后没有残留——服务、工具、监听器一起消失。

最后一条最重要。做不到，动态挂载就只是「装得上，拔不掉」。
"""

from __future__ import annotations

from dugentx.kernel.context import Context
from dugentx.kernel.loader import PluginRow, resolve_factory
from dugentx.kernel.plugin import define_plugin
from dugentx.plugins.self_extension import DynamicPlugins, manage_plugin
from dugentx.seams.agent import Agent, AgentConfig, AgentRegistry
from dugentx.seams.session import JsonlSessionStore, SessionLog
from dugentx.seams.tools import Tool, ToolRegistry


def build(*, with_dynamic: bool = True) -> tuple[Context, Agent, ToolRegistry]:
    """一个够用的最小环境：工具注册表 + agent + （可选的）动态插件服务。"""
    ctx = Context("dyn")
    tools = ToolRegistry(ctx)
    ctx.provide("tools", tools)
    log = SessionLog()
    ctx.provide("session", log)

    if with_dynamic:
        ctx.provide("dynamicPlugins", DynamicPlugins(ctx))

    agents = AgentRegistry(ctx)
    ctx.provide("agents", agents)
    agent = agents.create(session=log, config=AgentConfig(model="none"))
    ctx.provide("agent", agent)
    return ctx, agent, tools


# 一个用来被挂载的示例插件：它提供一个新服务，并注册一个新工具。
@define_plugin("weather", inject=("tools",), provides=("weather",))
def weather_plugin(ctx: Context, config: dict) -> None:
    ctx.provide("weather", {"city": config.get("city", "天津")})
    tool = Tool(
        name="get_weather",
        description="查天气",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=lambda: "晴",
        labels=frozenset({"read"}),
    )
    ctx.effect(lambda inner: inner.service("tools").register(tool), label="register:get_weather")


@define_plugin("needs-missing", inject=("nope",), provides=("whatever",))
def broken_plugin(ctx: Context, config: dict) -> None:
    ctx.provide("whatever", 1)


# ------------------------------------------------------------------ 测试


async def test_mounting_a_plugin_makes_its_service_and_tool_visible_immediately() -> None:
    ctx, agent, tools = build()
    assert tools.names() == []
    assert not ctx.has("weather")

    target = agent.ctx
    record = await ctx.service("dynamicPlugins").mount(target, __name__ + ":weather_plugin")

    assert ctx.service("weather") == {"city": "天津"}
    assert tools.names() == ["get_weather"]
    assert "get_weather" in [t["function"]["name"] for t in agent.visible_tools()]
    assert record["id"] == "weather_plugin"


async def test_unmounting_leaves_no_residue() -> None:
    """拔干净：服务、工具一起消失。这是「注册即可撤销」的最终验收。"""
    ctx, agent, tools = build()
    dynamic: DynamicPlugins = ctx.service("dynamicPlugins")

    await dynamic.mount(agent.ctx, __name__ + ":weather_plugin")
    assert ctx.has("weather") and tools.names() == ["get_weather"]

    dynamic.unmount("weather_plugin")
    assert not ctx.has("weather")
    assert tools.names() == []
    assert "get_weather" not in [t["function"]["name"] for t in agent.visible_tools()]


async def test_mount_and_unmount_are_recorded_in_the_session() -> None:
    """不留痕的挂载 = 事后无法解释的运行期状态。"""
    ctx, agent, _tools = build()
    await manage_plugin(ctx, "mount", module=__name__ + ":weather_plugin", plugin_id="w")
    reply = await manage_plugin(ctx, "unmount", plugin_id="w")

    kinds = [e.kind for e in agent.session]
    assert kinds.count("plugin/mounted") == 1
    assert kinds.count("plugin/unmounted") == 1
    mounted = agent.session.of_kind("plugin/mounted")[0]
    assert mounted.data["id"] == "w"
    assert mounted.data["provides"] == ["weather"]
    assert "已拔掉" in reply


async def test_unmounting_something_that_is_not_mounted_says_so() -> None:
    from dugentx.kernel.errors import ToolError

    ctx, _agent, _ = build()
    try:
        await manage_plugin(ctx, "unmount", plugin_id="ghost")
    except ToolError as exc:
        assert "ghost" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("应当报错")


async def test_mounting_a_plugin_with_an_unsatisfied_dependency_fails_loud() -> None:
    """装载时就失败，而不是留下一个半死不活的插件。"""
    from dugentx.kernel.errors import PluginError

    ctx, agent, _ = build()
    try:
        await ctx.service("dynamicPlugins").mount(agent.ctx, __name__ + ":broken_plugin")
    except PluginError as exc:
        assert "nope" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("应当报错")
    assert not ctx.has("whatever")


async def test_double_mount_refuses_instead_of_shadowing() -> None:
    from dugentx.kernel.errors import ToolError

    ctx, _agent, _ = build()
    await manage_plugin(ctx, "mount", module=__name__ + ":weather_plugin", plugin_id="w")
    try:
        await manage_plugin(ctx, "mount", module=__name__ + ":weather_plugin", plugin_id="w")
    except ToolError as exc:
        assert "已经挂着" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("应当报错")


async def test_list_shows_what_the_agent_currently_has() -> None:
    """`list` 是给自己看的镜子：有哪些服务、哪些工具、挂过什么。"""
    ctx, _agent, _tools = build()
    await manage_plugin(ctx, "mount", module=__name__ + ":weather_plugin", plugin_id="weather")

    view = await manage_plugin(ctx, "list")

    assert "工具" in view
    assert "get_weather" in view
    assert "运行期挂上的插件：weather" in view


async def test_unmounting_the_self_extension_plugin_takes_the_dynamic_ones_with_it() -> None:
    """动态机制自己也要守规矩：它被拔掉时，它挂上来的一起拔掉。"""
    ctx, agent, tools = build(with_dynamic=False)
    from dugentx.plugins.self_extension import create as self_extension

    dispose = await ctx.mount(self_extension({}))
    dynamic: DynamicPlugins = ctx.service("dynamicPlugins")
    await dynamic.mount(agent.ctx, __name__ + ":weather_plugin", plugin_id="w")
    # 注意工具表里有两个：self_extension 自己注册的 manage_plugin，加上刚挂上来的。
    assert tools.names() == ["get_weather", "manage_plugin"]

    dispose()  # 拔掉 self_extension 自己
    assert tools.names() == []
    assert not ctx.has("weather")
    assert not ctx.has("dynamicPlugins")


async def test_dynamic_plugins_survive_a_session_reload_in_the_log() -> None:
    """挂载是日志里的事实，所以重放一个会话能看出当时多过什么。"""
    import tempfile
    from pathlib import Path

    ctx, agent, _ = build()
    await manage_plugin(ctx, "mount", module=__name__ + ":weather_plugin", plugin_id="weather")

    with tempfile.TemporaryDirectory() as tmp:
        store = JsonlSessionStore(Path(tmp))
        store.save(agent.session)
        reloaded = store.load(agent.session.session_id)

    kinds = [e.kind for e in reloaded]
    assert "plugin/mounted" in kinds
    assert reloaded.of_kind("plugin/mounted")[0].data["module"].endswith("weather_plugin")


def test_resolve_factory_accepts_both_forms() -> None:
    """`模块` 和 `模块:属性` 两种写法都要能用——动态挂载靠它解析目标。"""
    factory = resolve_factory(__name__ + ":weather_plugin")
    # 工厂的 __name__ 是插件的名字，不是函数名：`define_plugin("weather", ...)`
    assert factory.__name__ == "weather"
    assert callable(resolve_factory("dugentx.plugins.tools"))


def test_plugin_row_rejects_a_row_without_id() -> None:
    import pytest

    from dugentx.kernel.errors import PluginError

    with pytest.raises(PluginError):
        PluginRow.from_mapping({"plugin": "x"}, where="test")
