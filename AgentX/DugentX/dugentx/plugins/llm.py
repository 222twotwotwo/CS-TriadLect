"""llm 插件 —— 把「用哪一个模型适配器」变成配置里的一行。

模型这一层的可替换性必须落在配置上，而不是代码里：离线测试要挂回放适配器、
生产要挂 any-llm、某个会话想临时换个小模型——这些都不该改任何调用方。
所以这里只做三件事：读配置、建适配器、把它放进 `ctx.llm` 这个注册表。

配置长这样（`dugentx.yml` 里的一行）：

```yaml
- id: llm
  plugin: dugentx.plugins.llm
  config:
    adapter: anyllm           # 或者 replay
    model: deepseek-chat
    provider: deepseek
    temperature: 0.2
    api_key_env: DEEPSEEK_API_KEY   # 放的是**变量名**，不是 key 本身
```

适配器自己的设置既可以写在顶层（上面的写法），也可以收在一个同名的块里
（`replay: {turns: [...], on_exhausted: repeat}`）——块里的键优先。
两种都支持是因为：常见的 anyllm 配置很短，不该被逼着多缩进一层；
而回放脚本是一大坨数据，混在顶层会淹掉别的键。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from dugentx.kernel.context import Context
from dugentx.kernel.errors import PluginError
from dugentx.kernel.plugin import define_plugin
from dugentx.providers.llm_anyllm import AnyLlmAdapter
from dugentx.providers.llm_replay import ReplayAdapter
from dugentx.seams.llm import LlmAdapter, LlmRegistry


def _build_anyllm(settings: dict[str, Any]) -> LlmAdapter:
    """真实调用：交给 any-llm。缺 model / provider 会在构造时大声报错。"""
    return AnyLlmAdapter(
        model=str(settings.get("model") or ""),
        provider=str(settings.get("provider") or ""),
        temperature=_optional_float(settings.get("temperature"), "temperature"),
        max_tokens=_optional_int(settings.get("max_tokens"), "max_tokens"),
        reasoning_effort=_optional_str(settings.get("reasoning_effort")),
        api_key_env=_optional_str(settings.get("api_key_env")),
    )


def _build_replay(settings: dict[str, Any]) -> LlmAdapter:
    """离线回放：脚本在配置里，一轮一次调用。"""
    turns = settings.get("turns")
    return ReplayAdapter(
        turns if turns is not None else None,
        name="replay",
        on_exhausted=str(settings.get("on_exhausted") or "error"),
    )


ADAPTERS: dict[str, Callable[[dict[str, Any]], LlmAdapter]] = {
    "anyllm": _build_anyllm,
    "replay": _build_replay,
}
"""能用的适配器。报错时把它整个打出来——「有哪些可选」比「你写错了」有用得多。"""


def _adapter_settings(config: dict[str, Any], name: str) -> dict[str, Any]:
    """取出当前适配器的设置：顶层键 + 同名块（块优先）。"""
    top = {key: value for key, value in config.items() if key != "adapter"}
    block = config.get(name)
    if isinstance(block, dict):
        return {**top, **block}
    return top


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_float(value: Any, field: str) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        raise PluginError(f"llm 配置里的 {field} 要是一个数，收到 {value!r}") from None


def _optional_int(value: Any, field: str) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise PluginError(f"llm 配置里的 {field} 要是一个整数，收到 {value!r}") from None


@define_plugin(
    "llm",
    provides=("llm",),
    description="模型适配器注册表：anyllm（真实调用，唯一碰 any-llm 的地方）或 replay（离线回放）",
)
def create(ctx: Context, config: dict[str, Any] | None = None) -> None:
    """装载模型层：建注册表，把配置选中的适配器注册成默认。

    适配器的注册也走 `ctx.effect`，所以拔掉这个插件时，注册表会一起空掉——
    卸载的对称性是从这种小事上积累出来的。
    """
    settings = dict(config or {})
    name = str(settings.get("adapter") or "anyllm")
    builder = ADAPTERS.get(name)
    if builder is None:
        raise PluginError(
            f"不认识 llm 适配器 {name!r}；可用的有 {sorted(ADAPTERS)}。"
            f"写 `adapter: replay` 可以完全离线地跑。"
        )

    adapter = builder(_adapter_settings(settings, name))
    registry = LlmRegistry()
    # 循环在没有显式模型名时会读它（见 agent_loop_basic 的兜底）。
    # 之所以挂在注册表上而不是新增一个服务：换模型是「这一层」的事，
    # 让循环去 import 适配器的字段就又把两层焊死了。
    registry.default_model = getattr(adapter, "model", "")

    ctx.effect(
        lambda _ctx: registry.register(adapter, default=True), label=f"llm:adapter:{name}"
    )
    ctx.provide("llm", registry)
