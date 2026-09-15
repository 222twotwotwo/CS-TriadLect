"""运行期换模型 —— 「这个会话现在在跟哪一家说话」。

`ctx.llm` 本来就装得下多个适配器（`LlmRegistry.register` / `use`），
所以换 provider **不需要新机制**：把新适配器注册进去、把默认切过去。
这个文件只是把那几步包成一个能被人和界面调用的东西。

真正需要小心的是**模型名必须跟着适配器走**。循环取的是

    model = agent.config.model or registry.default_model

而 agent 的模型名通常来自配置里写死的那一行（`dugentx.yml` 里是 `deepseek-flash`）。
只换适配器、不改这个字段，后果是**拿新 provider 去请求旧模型名**——
报出来的错看着像「这家不支持这个模型」，真相是我们自己没把状态改干净。
所以下面 `_apply` 里那几行赋值不是可选的收尾动作，是这件事的主要工作。

为什么不让界面自己干这件事：那样「换模型」就有了两份实现，一份会记得
同步模型名、另一份迟早忘记。缝在这里，界面只负责问「换成谁」。
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from dugentx.kernel.context import Context
from dugentx.kernel.effect import Disposer
from dugentx.kernel.errors import PluginError
from dugentx.seams.llm import LlmAdapter, LlmRegistry

BuildAdapter = Callable[[dict[str, Any]], LlmAdapter]
"""按一份配置造一个适配器。由 `llm` 插件提供——只有它认识具体适配器。"""

ListProviders = Callable[[], list[str]]
"""可选 provider 名。同样由 `llm` 插件转交，最终来自唯一那个 import any_llm 的文件。"""


@dataclass(frozen=True, slots=True)
class ModelChoice:
    """「现在在跟谁说」的完整答案。三个字段都可能缺，所以每个都有兜底。"""

    provider: str = ""
    model: str = ""
    adapter: str = ""

    def describe(self) -> str:
        """一行给人看的描述。provider 缺失时退回适配器名（回放适配器就是这样）。"""
        if self.provider and self.model:
            return f"{self.provider}/{self.model}"
        if self.model:
            return self.model
        return self.adapter or "（还没配）"


class ModelSwitcher:
    """`ctx.models` —— 看现在用的是谁，以及换一个。

    它**不缓存**：每次都去注册表现问。缓存是这类东西最常见的错法——
界面上的模型名和真正发出去的模型名分叉之后，你会先怀疑模型，
再怀疑网络，最后才怀疑那行缓存。
    """

    def __init__(
        self,
        ctx: Context,
        *,
        build: BuildAdapter,
        providers: ListProviders,
        adapter_name: str = "anyllm",
    ) -> None:
        self._ctx = ctx
        self._build = build
        self._providers = providers
        self._adapter_name = adapter_name
        self._replaced: LlmAdapter | None = None
        """被我们换掉的那个适配器。只留最后一个——它是「撤回到换之前」用的。"""
        self._disposers: list[Disposer] = []
        """换出去的每一笔注册都要能撤销。攒着它们，卸载时一起放掉。"""

    # ------------------------------------------------------------------ 看

    def providers(self) -> list[str]:
        """可选的全部 provider 名。"""
        return list(self._providers())

    def adapters(self) -> list[str]:
        """已经注册在 `ctx.llm` 里的适配器名。"""
        return self._registry().names()

    def current(self) -> ModelChoice:
        """现在真正会被用到的那一个。

        模型名读的是**循环会读的那个字段**（`agent.config.model`），
        不是适配器自己记的那个——两者不一致时，出去的是前者。
        """
        registry = self._registry()
        try:
            adapter = registry.adapter()
        except LookupError:
            return ModelChoice()
        agent = self._ctx.get("agent")
        config = getattr(agent, "config", None)
        model = str(getattr(config, "model", "") or registry.default_model or "")
        return ModelChoice(
            provider=str(getattr(adapter, "provider", "") or ""),
            model=model,
            adapter=str(getattr(adapter, "name", "") or ""),
        )

    # ------------------------------------------------------------------ 换

    def switch(
        self,
        *,
        provider: str,
        model: str,
        api_key_env: str | None = None,
        temperature: float | None = None,
    ) -> ModelChoice:
        """换到一个新的 anyllm 适配器。

        构造失败（provider 不认识、model 为空、key 变量不存在）会在这里
        抛出来，**而不是**等下一次请求——和装载期校验同一个道理：
        这些错误现在就能判定，就不该留到用户面前变成「模型不太行」。
        """
        if not provider or not model:
            raise PluginError("换模型要同时给出 provider 和 model")

        settings: dict[str, Any] = {"provider": provider, "model": model}
        if api_key_env:
            settings["api_key_env"] = api_key_env
        if temperature is not None:
            settings["temperature"] = temperature

        adapter = self._build(settings)
        return self._install(adapter, provider=provider, model=model)

    def use(self, name: str) -> ModelChoice:
        """切到一个**已经注册**的适配器，不新建（比如切回 `replay`）。

        模型名保持不动：换适配器不等于换模型，回放适配器会把请求原样记下来。
        """
        registry = self._registry()
        adapter = registry.adapter(name)  # 名字不存在时它自己会报清楚
        registry.use(name)
        choice = ModelChoice(
            provider=str(getattr(adapter, "provider", "") or ""),
            model=str(getattr(adapter, "model", "") or registry.default_model or ""),
            adapter=name,
        )
        self._record(choice, verb="切到已注册的适配器")
        return choice

    # ------------------------------------------------------------------ 内部

    def dispose(self) -> None:
        """把换出去的注册全部撤销，回到装载时那个适配器。

        卸载对称性对动态机制自己同样成立：`switch()` 做了一笔注册，
        那就得有一处能把它收回来，否则这个插件被拔掉时注册表里会留着
        一个没人认领的适配器。
        """
        for undo in reversed(self._disposers):
            undo()
        self._disposers.clear()

    def _registry(self) -> LlmRegistry:
        return self._ctx.service("llm")

    def _install(self, adapter: LlmAdapter, *, provider: str, model: str) -> ModelChoice:
        """把适配器装进注册表，并把**所有**记录当前模型的地方一起改掉。"""
        registry = self._registry()
        previous: LlmAdapter | None = None
        with contextlib.suppress(LookupError):
            previous = registry.adapter()

        self._disposers.append(registry.register(adapter, default=True))
        registry.default_model = model
        self._replaced = previous

        # 循环优先读 agent 上的模型名，所以这一行不是锦上添花：
        # 少了它，新 provider 会收到旧模型名。
        agent = self._ctx.get("agent")
        config = getattr(agent, "config", None)
        if config is not None and hasattr(config, "model"):
            config.model = model

        choice = ModelChoice(provider=provider, model=model, adapter=adapter.name)
        self._record(choice, verb=f"从 {previous.name if previous else '（无）'} 换成")
        return choice

    def _record(self, choice: ModelChoice, *, verb: str) -> None:
        """记进日志、发到总线。

        日志是必须的：一个会话里换过模型，事后回看「当时到底问了谁」
        只有一个答案的出处，那就是这条。不记，这次切换就只活在内存和界面上。
        """
        session = self._ctx.get("session")
        if session is not None:
            session.append(
                "model/switched",
                provider=choice.provider,
                model=choice.model,
                adapter=choice.adapter,
            )
        self._ctx.events.emit("model/switched", choice.provider, choice.model)

        channel = self._ctx.get("human")
        if channel is not None:
            channel.note(f"{verb} {choice.describe()}", kind="info")
