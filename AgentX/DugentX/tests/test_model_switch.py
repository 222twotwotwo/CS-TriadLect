"""运行期换模型。

这里钉住的是一条最容易漏掉的不变量：**模型名必须跟着适配器走。**

循环读的是

    model = agent.config.model or registry.default_model

而 agent 的模型名通常来自配置里写死的那一行。只换适配器、不改那个字段，
后果是拿新 provider 去请求旧模型名——报出来的错指向「模型」，
真正的原因却是我们没把状态改干净。所以下面第一条测试盯的就是这件事。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest

from dugentx.events import EVENT_MODES
from dugentx.kernel.context import Context
from dugentx.kernel.errors import PluginError
from dugentx.kernel.events import EventBus
from dugentx.providers.model_switch import ModelSwitcher
from dugentx.seams.agent import Agent, AgentConfig
from dugentx.seams.llm import Delta, LlmRegistry, LlmRequest
from dugentx.seams.session import SessionLog


class StubAdapter:
    """一个不说话的适配器：只用来占住 `ctx.llm` 里的一个名字。"""

    def __init__(self, *, name: str, provider: str = "", model: str = "") -> None:
        self.name = name
        self.provider = provider
        self.model = model

    async def stream(self, request: LlmRequest) -> AsyncIterator[Delta]:
        for chunk in ():  # pragma: no cover - 这个替身永远不该被真的调用
            yield chunk


def build(*, model: str = "deepseek-flash") -> SimpleNamespace:
    """搭一个够 `ModelSwitcher` 跑的最小上下文。"""
    ctx = Context("test", events=EventBus(EVENT_MODES))
    log = SessionLog("s")
    ctx.provide("session", log)

    registry = LlmRegistry(default_model=model)
    ctx.provide("llm", registry)
    registry.register(StubAdapter(name="replay", model=model), default=True)

    agent = Agent(ctx, log, AgentConfig(model=model))
    ctx.provide("agent", agent)

    asked: list[dict[str, Any]] = []

    def build_adapter(settings: dict[str, Any]) -> StubAdapter:
        asked.append(settings)
        return StubAdapter(
            name="anyllm",
            provider=str(settings.get("provider", "")),
            model=str(settings.get("model", "")),
        )

    switcher = ModelSwitcher(
        ctx, build=build_adapter, providers=lambda: ["deepseek", "openai"]
    )
    return SimpleNamespace(
        ctx=ctx, log=log, registry=registry, agent=agent, models=switcher, asked=asked
    )


def test_current_reports_what_the_loop_would_actually_send() -> None:
    """`current()` 读的必须是循环读的那个字段，不是适配器自我介绍的那个。"""
    s = build(model="deepseek-flash")
    s.agent.config.model = "换过的名字"  # 循环会用它，适配器上的还是旧的

    assert s.models.current().model == "换过的名字"


def test_switching_moves_the_model_name_along_with_the_adapter() -> None:
    """只换适配器不换模型名，等于拿新 provider 请求旧模型。"""
    s = build(model="deepseek-flash")

    choice = s.models.switch(provider="openai", model="gpt-4o")

    assert choice.provider == "openai"
    assert choice.model == "gpt-4o"
    # 两个地方都得动：一个是循环的兜底，一个是循环的首选。
    assert s.registry.default_model == "gpt-4o"
    assert s.agent.config.model == "gpt-4o"
    assert s.registry.adapter().provider == "openai"


def test_a_switch_is_recorded_so_it_can_be_explained_later() -> None:
    """换过模型的会话，事后要能查出「当时到底问了谁」。"""
    s = build()
    seen: list[tuple[str, str]] = []
    s.ctx.events.on("model/switched", lambda provider, model: seen.append((provider, model)))

    s.models.switch(provider="openai", model="gpt-4o")

    assert seen == [("openai", "gpt-4o")]
    events = [e for e in s.log.events() if e.kind == "model/switched"]
    assert events and events[-1].data["model"] == "gpt-4o"


def test_a_bad_switch_changes_nothing_at_all() -> None:
    """校验必须在动手之前：失败的切换不该留下半个状态。"""
    s = build(model="deepseek-flash")

    with pytest.raises(PluginError):
        s.models.switch(provider="openai", model="")

    assert s.registry.default_model == "deepseek-flash"
    assert s.agent.config.model == "deepseek-flash"
    assert s.asked == []  # 连适配器都没造


def test_a_switch_that_fails_while_building_also_leaves_no_trace() -> None:
    """构造适配器时抛错（比如缺 key），同样不能改坏现有状态。"""
    s = build(model="deepseek-flash")

    def explode(settings: dict[str, Any]) -> StubAdapter:
        raise PluginError("这个 provider 要 key")

    s.models = ModelSwitcher(s.ctx, build=explode, providers=lambda: ["openai"])
    with pytest.raises(PluginError):
        s.models.switch(provider="openai", model="gpt-4o")

    assert s.registry.default_model == "deepseek-flash"
    assert s.agent.config.model == "deepseek-flash"


def test_dispose_puts_the_registry_back_where_it_started() -> None:
    """换出去的那笔注册要能收回来——否则卸载时会留下没人认领的适配器。"""
    s = build(model="deepseek-flash")
    s.models.switch(provider="openai", model="gpt-4o")
    assert s.registry.default_name == "anyllm"

    s.models.dispose()

    assert s.registry.default_name == "replay"
    assert s.registry.names() == ["replay"]


def test_use_switches_to_an_adapter_that_is_already_registered() -> None:
    """已经有回放适配器时，「切回去」不该再造一个新的。"""
    s = build(model="deepseek-flash")
    s.registry.register(StubAdapter(name="second", model="别的"), default=False)

    choice = s.models.use("second")

    assert choice.adapter == "second"
    assert s.registry.default_name == "second"
    assert s.asked == []
