"""permissions 插件 —— 提供权限策略和审批者，并把门装到工具管道上。

这是「插件，而不是改循环」最直接的一个样板：agent 循环不知道权限存在，
工具也不知道权限存在，只有这里知道。门装在 `tools/pre-execute` 上，
所以新加一个打了 `write` 标签的工具，这套分级自动管住它。

**装载顺序：必须在 `tools` 之后。** 门每次都要用 `ctx.service("tools")`
去查这次调用的标签，`inject=("tools",)` 把这个顺序写成了契约，装载器据此
拓扑排序。运行期动态挂载时，如果目标上下文里还没有 `tools`，它会当场失败——
那是有意的：一个查不到标签的门只能靠猜，而猜错的方向是放行。

配置：

- `levels`：标签 → 档位 的覆盖，例如 `{"write": "auto"}`。
- `default`：没有标签的工具算哪一档，默认 `confirm`。
- `mode`：`"policy"`（按答案表回答）/ `"interactive"`（在终端上问）
  / `"channel"`（走 human 缝问人——TUI 与 stdio 通道都从这儿进来）。
- `answers`：policy 模式下每个档位的答案，写成 `refuse` / `allow`（也接受
  `true` / `false`），例如 `{"auto": allow, "confirm": allow, "deny": refuse}`。
- `tool_overrides`：按工具名的个别答案，例如 `{"run_command": refuse}`。
- `argument_rules`：按参数内容的规则，例如
  `[{"tool": "run_command", "contains": "rm -rf", "allowed": false}]`。
  工具标签是构造时定的，而命令危不危险取决于命令文本，所以按文本拦只能在这里。

`mode: interactive` 在非终端环境（CI、管道）里会一律拒绝——这是有意的，
理由写在 `approval_interactive.py` 里。
"""

from __future__ import annotations

from typing import Any

from dugentx.kernel.context import Context
from dugentx.kernel.errors import PluginError
from dugentx.kernel.plugin import define_plugin
from dugentx.providers.approval_channel import ChannelApproval
from dugentx.providers.approval_interactive import InteractiveApproval
from dugentx.providers.approval_policy import DEFAULT_ANSWERS, ArgumentRule, PolicyApproval
from dugentx.seams.permissions import Level, PermissionPolicy, install_gate

_LEVELS = ("auto", "confirm", "deny")

_TRUE_WORDS = frozenset({"allow", "allowed", "yes", "y", "true", "on", "1"})
_FALSE_WORDS = frozenset({"refuse", "refused", "deny", "denied", "no", "n", "false", "off", "0"})
"""人写配置时用的是 `allow` / `refuse`，不是 `true` / `false`。两种都认。

为什么要一张表而不是 `bool(value)`：`bool("refuse")` 是 True。配置里一个
拼错的词会把「拒绝」悄悄读成「放行」，而那是这份代码里最不能出错的一处——
宁可装载失败，也不要一个把 deny 档读成放行的权限插件。
"""


def _level(value: Any, where: str) -> Level:
    """校验一个档位名。

    写错档位（`"confirm "`、`"warn"`）在运行期的表现是「这一档永远不匹配」，
    也就是悄悄降级到 default。所以这里在装载时就把拼写错误揪出来。
    """
    text = str(value)
    if text not in _LEVELS:
        raise PluginError(f"permissions 的 {where} 必须是 {list(_LEVELS)} 之一，收到 {value!r}")
    return text  # type: ignore[return-value]


def _flag(value: Any, where: str) -> bool:
    """把配置里的一个回答（allow / refuse，或 true / false）解析成 bool。"""
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUE_WORDS:
        return True
    if text in _FALSE_WORDS:
        return False
    raise PluginError(
        f"permissions 的 {where} 只能是 allow / refuse（也接受 true / false），收到 {value!r}"
    )


def _rules(raw: Any) -> tuple[ArgumentRule, ...]:
    """把配置里的参数规则解析成 `ArgumentRule`。"""
    rules: list[ArgumentRule] = []
    for i, item in enumerate(raw or ()):
        if not isinstance(item, dict) or "contains" not in item:
            raise PluginError(
                f"permissions 的 argument_rules[{i}] 必须是一个含 contains 的映射：{item!r}"
            )
        rules.append(
            ArgumentRule(
                tool=str(item.get("tool", "*")),
                contains=str(item["contains"]),
                allowed=bool(item.get("allowed", False)),
                reason=str(item.get("reason", "")),
            )
        )
    return tuple(rules)


@define_plugin(
    "permissions",
    inject=("tools",),
    provides=("permissions", "approval"),
    description="按工具标签分级，把审批挂在 tools/pre-execute 上（必须装在 tools 之后）",
)
def create(ctx: Context, config: dict[str, Any]) -> None:
    """装载权限策略和审批者，并把门挂上。"""
    policy = PermissionPolicy(default=_level(config.get("default", "confirm"), "default"))
    for label, value in (config.get("levels") or {}).items():
        policy.levels[str(label)] = _level(value, f"levels.{label}")

    mode = str(config.get("mode", "policy"))
    approval: PolicyApproval | InteractiveApproval | ChannelApproval
    if mode == "policy":
        approval = PolicyApproval(
            answers={
                str(key): _flag(value, f"answers.{key}")
                for key, value in (config.get("answers") or DEFAULT_ANSWERS).items()
            },
            tool_overrides={
                str(key): _flag(value, f"tool_overrides.{key}")
                for key, value in (config.get("tool_overrides") or {}).items()
            },
            argument_rules=_rules(config.get("argument_rules")),
        )
        for key in approval.answers:
            _level(key, f"answers.{key}")
    elif mode == "interactive":
        approval = InteractiveApproval()
    elif mode == "channel":
        # 「问人」这件事交给 human 缝。**通道是 lazy 取的**：装载顺序由
        # inject 推导，但审批只在真要问的时候才需要人。晚一点拿，
        # 配置里就不用为了顺序操心——而且 TUI 通道也能在同一个位置被换上来。
        approval = ChannelApproval(lambda: ctx.get("human"), events=ctx.events)
    else:
        raise PluginError(
            f"permissions 的 mode 只能是 'policy' / 'interactive' / 'channel'，收到 {mode!r}"
        )

    ctx.provide("permissions", policy)
    ctx.provide("approval", approval)

    # 门是通过 ctx.on 挂上去的，所以它挂在这个插件的子作用域里：
    # 卸载 permissions，门跟着消失，`tools` 管道回到没有权限的样子。
    install_gate(ctx, policy=policy, approval=approval)
