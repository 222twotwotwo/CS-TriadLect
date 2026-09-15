"""permissions 缝 —— 权限分级，开关在代码侧。

这是整份代码里最该被记住的一条论点：

> **写在提示词里的禁令不算控制，因为模型可以选择不遵守。**
> 写在 `tools/pre-execute` 上的分级才算，因为那是代码。

原始出处是 OpenAI 2023-06-13 的 function calling 公告：同一篇文章里既宣布了
工具调用，也写明了工具输出里的不可信内容可以指示模型做不该做的事，
并建议开发者在**执行有真实后果的动作之前**插入用户确认步骤。
这句话五年后依然是权限设计的第一原则。

分级只有三档，因为人只分得清三档：

- `auto`   只读，直接放行（读文件、查目录、搜索）
- `confirm` 有副作用，先问一句（写文件、改文件、跑普通命令）
- `deny`   危险，默认拒绝（删目录、动网络凭据、执行陌生脚本）

分档依据是**工具标签**，不是工具名字。新加一个写文件的工具，
只要带上 `write` 标签，这套策略自动管住它——不需要改这里一行。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

from dugentx.kernel.context import Context
from dugentx.seams.tools import LABEL_DANGEROUS, LABEL_NETWORK, LABEL_READ, LABEL_WRITE

Level = Literal["auto", "confirm", "deny"]


@dataclass(slots=True)
class ApprovalRequest:
    """一次待批准的调用。给人和给日志看的是同一个对象。"""

    tool: str
    labels: frozenset[str]
    arguments: dict[str, object]
    level: Level
    summary: str = ""
    agent_label: str = "main"

    def render(self) -> str:
        args = ", ".join(f"{k}={_short(v)}" for k, v in self.arguments.items())
        head = f"{self.tool}({args})"
        return f"{head}  —  {self.summary}" if self.summary else head


@dataclass(slots=True)
class Decision:
    allowed: bool
    level: Level
    reason: str = ""

    @classmethod
    def allow(cls, level: Level = "auto", reason: str = "") -> Decision:
        return cls(allowed=True, level=level, reason=reason)

    @classmethod
    def refuse(cls, level: Level, reason: str) -> Decision:
        return cls(allowed=False, level=level, reason=reason)


@runtime_checkable
class Approval(Protocol):
    """`ctx.approval` —— 谁来回答「这次能不能跑」。

    实现可以是交互式终端提问、可以是一个「全部同意」的 CI 模式，
    也可以是一个把请求转给外部的钩子。缝在这里，形态不限。
    """

    async def decide(self, request: ApprovalRequest) -> Decision: ...


@dataclass(slots=True)
class PermissionPolicy:
    """按标签分档的策略。这是 `ctx.permissions`。"""

    levels: dict[str, Level] = field(
        default_factory=lambda: {
            LABEL_READ: "auto",
            LABEL_WRITE: "confirm",
            LABEL_NETWORK: "confirm",
            LABEL_DANGEROUS: "deny",
        }
    )
    default: Level = "confirm"
    """没有标签的工具按这个算。默认「要问一句」而不是「放行」——
    漏拦一个写操作的代价，比多问一句大得多。"""

    def level_for(self, labels: frozenset[str]) -> Level:
        if not labels:
            return self.default
        # 取最严的那一档：同时带 read 和 write 的工具按 write 算
        severity = {"auto": 0, "confirm": 1, "deny": 2}
        return max(
            (self.levels.get(label, self.default) for label in labels),
            key=severity.__getitem__,
        )

    def describe(self) -> str:
        parts = [f"{k}→{v}" for k, v in sorted(self.levels.items())]
        return "、".join(parts) + f"、其他→{self.default}"


ApprovalHook = Callable[[ApprovalRequest], object]


def _short(value: object, limit: int = 48) -> str:
    text = repr(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def install_gate(
    ctx: Context,
    *,
    policy: PermissionPolicy,
    approval: Approval,
) -> None:
    """把权限装到 `tools/pre-execute` 上。

    这是「插件，而不是改循环」的样板：循环不知道权限存在，
    工具不知道权限存在，只有这一个函数知道。

    参数名用 `nxt` 而不是 `next`：后者是 Python 内建函数，
    一旦被同名参数遮住，`await next()` 会变成「对一个迭代器取下一个元素」，
    报出来的错是 `next expected at least 1 argument`——差得很远，很难联想。
    """
    from dugentx.kernel.errors import PermissionDenied
    from dugentx.seams.messages import ToolCall

    async def gate(call: ToolCall, nxt):  # type: ignore[no-untyped-def]
        registry = ctx.service("tools")
        try:
            tool = registry.get(call.name)
        except Exception:
            # 不认识的工具不归权限管：让下游去报「没有这个工具」，
            # 那个错误对模型更有用。
            return await nxt()
        level = policy.level_for(tool.labels)
        request = ApprovalRequest(
            tool=call.name,
            labels=tool.labels,
            arguments=call.arguments,
            level=level,
        )
        if level == "auto":
            ctx.events.emit("permission/skip", request)
            return await nxt()
        decision = await approval.decide(request)
        ctx.events.emit("permission/decided", request, decision)
        if not decision.allowed:
            raise PermissionDenied(decision.reason or f"{call.name} 被权限策略拒绝（{level}）")
        return await nxt()

    ctx.on("tools/pre-execute", gate, prepend=True)
