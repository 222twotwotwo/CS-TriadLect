"""内核测试：上下文、effect、事件、装载器。

这些是「可拔插」本身的测试——它们不碰模型、不碰网络，跑得飞快，
但一旦坏掉，后面所有东西都会以奇怪的方式出问题。
"""

from __future__ import annotations

import pytest

from dugentx.kernel.context import Context
from dugentx.kernel.errors import DuGentXError, PluginError, ServiceNotFound
from dugentx.kernel.events import EventBus
from dugentx.kernel.loader import PluginRow, load_composition, order_plugins
from dugentx.kernel.plugin import Plugin, define_plugin

# ------------------------------------------------------------------ 上下文


def test_service_is_found_by_key_not_by_import() -> None:
    """换实现只换提供者，调用方不需要知道它是谁。"""
    ctx = Context("t")
    ctx.provide("greeter", lambda: "你好")
    assert ctx.service("greeter")() == "你好"

    ctx.provide("greeter2", lambda: "hello")
    assert ctx.service("greeter2")() == "hello"


def test_getattr_sugar_resolves_only_registered_services() -> None:
    ctx = Context("t")
    ctx.provide("tools", object())
    assert ctx.tools is ctx.service("tools")
    with pytest.raises(AttributeError):
        _ = ctx.nope  # type: ignore[attr-defined]


def test_missing_service_fails_loud_with_the_available_list() -> None:
    ctx = Context("t")
    ctx.provide("a", 1)
    with pytest.raises(ServiceNotFound) as info:
        ctx.service("b")
    assert "a" in str(info.value)


def test_one_provider_per_key() -> None:
    """同一个键只能有一个提供者——这正是「换掉实现」的方式：改配置，不是叠加。"""
    ctx = Context("t")
    ctx.provide("x", 1)
    with pytest.raises(PluginError):
        ctx.provide("x", 2)


def test_disposer_removes_only_its_own_service() -> None:
    ctx = Context("t")
    dispose = ctx.provide("x", 1)
    dispose()
    assert not ctx.has("x")


# ------------------------------------------------------------------ effect


def test_effects_unwind_in_reverse_order() -> None:
    """后进先出：先建立的东西，最后拆。"""
    order: list[str] = []
    ctx = Context("t")
    ctx.effect(lambda _c: (lambda: order.append("first")), label="first")
    ctx.effect(lambda _c: (lambda: order.append("second")), label="second")
    ctx.dispose()
    assert order == ["second", "first"]


def test_child_scope_dies_with_parent() -> None:
    ctx = Context("t")
    seen: list[str] = []
    child = ctx.child("kid")
    child.effect(lambda _c: (lambda: seen.append("kid")))
    ctx.dispose()
    assert seen == ["kid"]


def test_disposing_twice_is_harmless() -> None:
    ctx = Context("t")
    ctx.dispose()
    ctx.dispose()


def test_cannot_register_into_a_closed_scope() -> None:
    ctx = Context("t")
    ctx.dispose()
    with pytest.raises(DuGentXError):
        ctx.provide("late", 1)


# ------------------------------------------------------------------ 事件


def test_emit_calls_listeners_in_registration_order() -> None:
    bus = EventBus()
    seen: list[str] = []
    bus.on("e", lambda: seen.append("a"))
    bus.on("e", lambda: seen.append("b"))
    bus.emit("e")
    assert seen == ["a", "b"]


def test_prepend_jumps_the_queue() -> None:
    bus = EventBus()
    seen: list[str] = []
    bus.on("e", lambda: seen.append("first"))
    bus.on("e", lambda: seen.append("zero"), prepend=True)
    bus.emit("e")
    assert seen == ["zero", "first"]


def test_disposer_stops_the_listener() -> None:
    bus = EventBus()
    seen: list[str] = []
    dispose = bus.on("e", lambda: seen.append("a"))
    bus.emit("e")
    dispose()
    bus.emit("e")
    assert seen == ["a"]


async def test_waterfall_delegates_and_returns_the_terminal_value() -> None:
    bus = EventBus()
    seen: list[str] = []

    async def outer(value: int, nxt):  # type: ignore[no-untyped-def]
        seen.append(f"outer:{value}")
        return await nxt()

    async def terminal(value: int) -> int:
        return value * 2

    bus.on("w", outer)
    assert await bus.waterfall("w", 21, terminal=terminal) == 42
    assert seen == ["outer:21"]


async def test_waterfall_can_rewrite_the_payload_for_downstream() -> None:
    """改写请求就是靠这个：`next(new_args)`。"""
    bus = EventBus()

    async def rewriter(value: int, nxt):  # type: ignore[no-untyped-def]
        return await nxt(value + 1)

    async def terminal(value: int) -> int:
        return value

    bus.on("w", rewriter)
    assert await bus.waterfall("w", 1, terminal=terminal) == 2


async def test_waterfall_short_circuits_when_a_listener_skips_next() -> None:
    """不调 next() 直接返回 = 短路。权限拒绝、策略拦截都是这个形态。"""
    bus = EventBus()
    reached: list[str] = []

    async def blocker(value: int, nxt):  # type: ignore[no-untyped-def]
        return -1

    async def terminal(value: int) -> int:
        reached.append("terminal")
        return value

    bus.on("w", blocker)
    assert await bus.waterfall("w", 5, terminal=terminal) == -1
    assert reached == []


async def test_bail_returns_the_first_non_none() -> None:
    bus = EventBus()

    async def first(_x: int) -> None:
        return None

    async def second(_x: int) -> str:
        return "赢了"

    async def third(_x: int) -> str:
        return "不该到这里"

    bus.on("b", first)
    bus.on("b", second)
    bus.on("b", third)
    assert await bus.bail("b", 1) == "赢了"


def test_event_catalog_rejects_a_typo() -> None:
    """事件名写错是静默失败，所以这里必须吵。"""
    from dugentx.events import EVENT_MODES

    bus = EventBus(EVENT_MODES)
    with pytest.raises(Exception) as info:
        bus.emit("tool/cals")  # 少一个 l
    assert "不在事件目录里" in str(info.value)


def test_event_catalog_rejects_a_wrong_dispatch_mode() -> None:
    from dugentx.events import EVENT_MODES

    bus = EventBus(EVENT_MODES)
    with pytest.raises(Exception) as info:
        bus.emit("tools/pre-execute")
    assert "派发方式" in str(info.value)


# ------------------------------------------------------------------ 插件与装载


async def test_mount_rejects_a_missing_dependency_before_running_any_code() -> None:
    ctx = Context("t")
    ran: list[str] = []

    @define_plugin("needs-fs", inject=("fs",))
    def create(c: Context, config: dict) -> None:
        ran.append("装上了")

    with pytest.raises(PluginError) as info:
        await ctx.mount(create({}))
    assert "fs" in str(info.value)
    assert ran == []


async def test_mount_hands_the_row_config_to_the_plugin() -> None:
    """配置是 mount 时递给 apply 的，插件不需要知道它从哪来。"""
    ctx = Context("t")
    seen: dict = {}

    @define_plugin("echo-config")
    def create(c: Context, config: dict) -> None:
        seen.update(config)

    await ctx.mount(create({"root": ".", "timeout": 60}))
    assert seen == {"root": ".", "timeout": 60}


async def test_unmount_undoes_everything_the_plugin_registered() -> None:
    """这是运行期动态挂载能成立的前提。

    注意插件里用了 `c.effect(...)` 而不是直接调 `register(...)`：
    **注册必须走 ctx 提供的入口，撤销才找得到它。**
    绕过去的东西卸载时不会被收走，那就是幽灵插件。
    """
    ctx = Context("t")
    ctx.provide("tools", _FakeTools())

    @define_plugin("weather", inject=("tools",), provides=("weather",))
    def create(c: Context, config: dict) -> None:
        c.provide("weather", "晴天")
        c.effect(lambda inner: inner.service("tools").register("get_weather"))
        c.on("tool/call", lambda *_: None)

    dispose = await ctx.mount(create({}))
    assert ctx.has("weather")
    assert ctx.service("tools").names() == ["get_weather"]

    dispose()
    assert not ctx.has("weather")
    assert ctx.service("tools").names() == []


async def test_a_plugin_that_bypasses_ctx_leaves_ghosts() -> None:
    """反面教材：直接调用注册接口，卸载时收不干净。

    这个测试不是在鼓励这么写，而是把「为什么要走 ctx」变成一条可执行的证据。
    """
    ctx = Context("t")
    ctx.provide("tools", _FakeTools())

    @define_plugin("sloppy", inject=("tools",))
    def create(c: Context, config: dict) -> None:
        c.service("tools").register("left_behind")  # 没有返回给 scope

    dispose = await ctx.mount(create({}))
    dispose()
    assert ctx.service("tools").names() == ["left_behind"]


def test_loader_orders_by_inject_and_refuses_an_unknown_service() -> None:
    rows = [
        PluginRow(id="consumer", plugin="dugentx.plugins.tools", inject=("nonexistent",)),
        PluginRow(id="provider", plugin="dugentx.plugins.tools"),
    ]
    plugins = [
        Plugin(name="consumer", apply=lambda c: None, inject=("nonexistent",)),
        Plugin(name="provider", apply=lambda c: None, provides=("exists",)),
    ]
    with pytest.raises(PluginError):
        order_plugins(rows, plugins)


def test_loader_puts_providers_before_consumers() -> None:
    rows = [
        PluginRow(id="b", plugin="x"),
        PluginRow(id="a", plugin="y"),
    ]
    plugins = [
        Plugin(name="b", apply=lambda c: None, inject=("svc",)),
        Plugin(name="a", apply=lambda c: None, provides=("svc",)),
    ]
    order = order_plugins(rows, plugins)
    assert [rows[i].id for i in order] == ["a", "b"]


def test_loader_detects_a_cycle() -> None:
    rows = [PluginRow(id="a", plugin="x"), PluginRow(id="b", plugin="y")]
    plugins = [
        Plugin(name="a", apply=lambda c: None, inject=("y"), provides=("x",)),
        Plugin(name="b", apply=lambda c: None, inject=("x"), provides=("y",)),
    ]
    with pytest.raises(PluginError) as info:
        order_plugins(rows, plugins)
    assert "成环" in str(info.value)


def test_two_plugins_cannot_declare_the_same_service() -> None:
    rows = [PluginRow(id="a", plugin="x"), PluginRow(id="b", plugin="y")]
    plugins = [
        Plugin(name="a", apply=lambda c: None, provides=("same",)),
        Plugin(name="b", apply=lambda c: None, provides=("same",)),
    ]
    with pytest.raises(PluginError):
        order_plugins(rows, plugins)


def test_missing_config_file_is_not_an_error_but_a_missing_row_field_is(tmp_path) -> None:
    composition = load_composition(None)
    assert composition.plugins == []

    bad = tmp_path / "bad.yml"
    bad.write_text("plugins:\n  - config: {}\n", encoding="utf-8")
    with pytest.raises(PluginError):
        load_composition(bad)


class _FakeTools:
    """一个最小可用的工具注册表替身，只为验证撤销语义。"""

    def __init__(self) -> None:
        self._names: list[str] = []

    def register(self, name: str):  # type: ignore[no-untyped-def]
        self._names.append(name)

        def dispose() -> None:
            if name in self._names:
                self._names.remove(name)

        return dispose

    def names(self) -> list[str]:
        return sorted(self._names)
