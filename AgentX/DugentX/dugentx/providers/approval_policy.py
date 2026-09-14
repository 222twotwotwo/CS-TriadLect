"""策略审批 —— 「没有人可问」时的那个回答者。

无人值守跑起来遇到的第一个问题不是模型，而是：谁来回答「这次能不能跑」。
答案是配置。按级别给一个静态答案（auto/confirm/deny → 允许或拒绝），
再加一组按工具名、按参数内容的覆盖。

它存在的理由是：没有它，任何带 `write` 标签的工具都会停在等一个人按下 y。
CI、测试、批量任务因此全都跑不动——不是权限设计错了，是缺一个愿意回答的人。
反过来，默认答案只放行 `auto`：想全放行必须**显式写出来**
（`{"auto": true, "confirm": true}`），因为「不小心全放行」的代价比
「多问一次」大得多。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from dugentx.seams.permissions import ApprovalRequest, Decision

DEFAULT_ANSWERS: dict[str, bool] = {"auto": True, "confirm": False, "deny": False}
"""默认答案：只读放行，其余拒绝。

保守的那一端才是好的默认值：一个拒绝是可以被模型和人都看见并纠正的，
而一次不该发生的写操作不会留下可以纠正的机会。
"""


def _arguments_text(request: ApprovalRequest) -> str:
    """把一次调用的参数摊成一段文本，供规则做子串匹配。

    用 JSON 而不是 `repr()`：这段文本要被人写进配置里当匹配目标，
    JSON 的引号和转义规则是众所周知的，Python 的 repr 不是。
    """
    return json.dumps(request.arguments, ensure_ascii=False, default=str)


@dataclass(frozen=True, slots=True)
class ArgumentRule:
    """按**参数内容**表决的规则。

    为什么需要它：工具标签是构造时定的，而「这条命令危不危险」取决于命令文本，
    构造期看不到。`run_command` 就是这么个工具——同一个工具既能跑
    `ls` 也能跑 `rm -rf /`。要按文本拦住后者，规则只能挂在能看见参数的地方，
    也就是这里。
    """

    tool: str
    contains: str
    allowed: bool
    reason: str = ""

    def matches(self, request: ApprovalRequest) -> bool:
        """工具名对不对得上（空串和 `*` 表示任意工具），参数文本里有没有那个片段。"""
        if self.tool not in ("", "*", request.tool):
            return False
        return self.contains.lower() in _arguments_text(request).lower()


@dataclass(slots=True)
class PolicyApproval:
    """按配置回答每一次审批。

    三层的先后顺序是有意的，从最具体到最笼统：
    参数规则 → 按工具名的覆盖 → 按级别的答案。越具体的规则越知道自己在说什么，
    所以它先说话。
    """

    answers: dict[str, bool] = field(default_factory=lambda: dict(DEFAULT_ANSWERS))
    tool_overrides: dict[str, bool] = field(default_factory=dict)
    argument_rules: tuple[ArgumentRule, ...] = ()

    async def decide(self, request: ApprovalRequest) -> Decision:
        """回答一次审批。这个方法永远是「立刻返回」的——它不做 I/O。"""
        for rule in self.argument_rules:
            if rule.matches(request):
                reason = rule.reason or f"命中了参数规则（参数里出现 {rule.contains!r}）"
                if rule.allowed:
                    return Decision.allow(request.level, reason)
                return Decision.refuse(request.level, reason)

        override = self.tool_overrides.get(request.tool)
        if override is not None:
            if override:
                return Decision.allow(request.level, f"配置把 {request.tool} 单独设成了允许")
            return Decision.refuse(request.level, f"配置把 {request.tool} 单独设成了拒绝")

        if self.answers.get(request.level, False):
            return Decision.allow(request.level, f"策略里 {request.level} 档的答案是允许")
        return Decision.refuse(
            request.level,
            f"策略里 {request.level} 档的答案是拒绝：{request.render()}",
        )
