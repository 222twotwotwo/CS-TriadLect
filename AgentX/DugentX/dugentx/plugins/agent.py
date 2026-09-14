"""agent 插件 —— 会话载体与 agent 名单。

这里做一件 dsh 里很关键、但在小项目里最容易被忽略的事：

> **每个 agent 有自己的子上下文（`agent.ctx`）。**

服务是共享的——`ctx.tools`、`ctx.fs` 只有一份，换实现是全局的事。
但**注册是分作用域的**。给这个 agent 单独挂一个插件、加一段系统提示词、
换一个模型，都应该只影响它。所以 agent 拿到的是一个 `ctx.child(...)`，
它上面挂的东西在 agent 结束时会一起撤销。

运行期动态挂载（`self_extension` 插件）就挂在这里：
agent 给自己装的能力，拔掉时干干净净。
"""

from __future__ import annotations

from typing import Any

from dugentx.kernel.context import Context
from dugentx.kernel.plugin import define_plugin
from dugentx.seams.agent import AgentConfig, AgentRegistry


def config_from(raw: dict[str, Any]) -> AgentConfig:
    """从配置映射出 AgentConfig，未知键直接报错。

    拼错的配置键如果被静默忽略，表现出来是「我明明配了，怎么没生效」——
    这类问题花掉的时间比装载时报错多十倍。
    """
    known = {
        "model",
        "max_steps",
        "temperature",
        "reasoning_effort",
        "tool_allow",
        "tool_deny",
        "prompt_extras",
        "context_budget_tokens",
        "label",
    }
    unknown = set(raw) - known
    if unknown:
        raise ValueError(f"agent 配置里有不认识的键：{sorted(unknown)}；可用：{sorted(known)}")
    return AgentConfig(
        model=str(raw.get("model", "")),
        max_steps=int(raw.get("max_steps", 24)),
        temperature=raw.get("temperature"),
        reasoning_effort=raw.get("reasoning_effort"),
        tool_allow=tuple(raw.get("tool_allow") or ()),
        tool_deny=tuple(raw.get("tool_deny") or ()),
        prompt_extras=dict(raw.get("prompt_extras") or {}),
        context_budget_tokens=int(raw.get("context_budget_tokens", 60_000)),
        label=str(raw.get("label", "main")),
    )


@define_plugin(
    "agent",
    inject=("session", "tools"),
    provides=("agents", "agent"),
    description="agent 名单，以及这次组合里的主 agent",
)
def create(ctx: Context, config: dict[str, Any] | None = None) -> None:
    """建立主 agent。

    `inject` 里只要了 `session` 和 `tools`——**没有要 `llm`**。
    这是故意的：agent 是被别人驱动的（driver 才是要 llm 的那个），
    agent 自己只持有会话和配置。依赖声明得越窄，这个插件能装的位置就越多。
    """
    registry = AgentRegistry(ctx)
    ctx.provide("agents", registry)

    session = ctx.service("session")
    agent_ctx = ctx.child("agent:main")
    agent = registry.create(
        session=session,
        config=config_from(config or {}),
        label=str((config or {}).get("label", "main")),
    )
    # 把 agent 挪到它自己的子上下文上：它挂的能力只属于它。
    agent.ctx = agent_ctx
    ctx.provide("agent", agent)
