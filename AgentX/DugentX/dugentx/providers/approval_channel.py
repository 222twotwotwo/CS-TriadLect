"""把人机通道包成审批者。

这一层薄得出奇，而它薄得对：审批的**政策**（哪一档要问、什么内容危险、
本会话里记住过什么）在 permissions 缝里；审批的**问法**在 human 缝里。
这里只做一件事——把 `ApprovalRequest` 翻译成一个三个选项的问题，
再把答案翻译回 `Decision`。

三种答案各自的含义值得写下来，因为它们不是「是/否」那么简单：

- **允许这一次**：放行，下次还问。这是默认选项。
- **本会话内都允许**：放行，并且记住这个工具，之后不再问。
  它必须存在——每次都要按一次 y 的审批，只会把人训练成不停按 y 的人，
  那时候这道门就只剩装饰作用了。
- **拒绝**：不放行。工具会收到一条「被拦截」的结果，模型看得到，
  于是它有机会换个办法，而不是整个会话断掉。

通道**按需获取**，不在构造时拿：装载顺序在配置里是推导出来的，
但审批只在真的要问的时候才需要人。晚一点拿，就不会为了一个顺序问题
去改配置。拿不到就拒绝——一个在 CI 里挂住等输入的 harness，
比一个当场说「不」的糟糕得多。
"""

from __future__ import annotations

from collections.abc import Callable

from dugentx.kernel.events import EventBus
from dugentx.seams.human import ALWAYS, APPROVAL_CHOICES, NO, YES, Answer, Question
from dugentx.seams.permissions import ApprovalRequest, Decision

Resolve = Callable[[], object | None]


class ChannelApproval:
    """满足 `Approval` 协议：把人机通道当作回答问题的那个人。"""

    def __init__(
        self,
        resolve: Resolve,
        *,
        events: EventBus | None = None,
        allow_always: bool = True,
    ) -> None:
        self._resolve = resolve
        self._events = events
        self._allow_always = allow_always
        self._session_allow: set[str] = set()

    @property
    def session_allow(self) -> frozenset[str]:
        return frozenset(self._session_allow)

    async def decide(self, request: ApprovalRequest) -> Decision:
        channel = self._resolve()
        if channel is None:
            return Decision.refuse(
                request.level, f"{request.tool} 需要确认，但这次组合里没有可问的人"
            )
        if not channel.interactive():  # type: ignore[attr-defined]
            return Decision.refuse(
                request.level,
                f"{request.tool} 需要确认，但当前不是交互式环境；"
                f"要无人值守地跑，就把这一档的答案写进配置",
            )

        if request.tool in self._session_allow:
            return Decision.allow(request.level, "本会话里已经允许过这个工具")

        question = self._question(request)
        self._emit("human/asked", question, self._name(channel))
        answer: Answer = await channel.choose(question)  # type: ignore[attr-defined]
        self._emit("human/answered", question, answer)
        return self._interpret(request, answer)

    # ---------------------------------------------------------------- 内部

    def _question(self, request: ApprovalRequest) -> Question:
        return Question(
            prompt=f"要执行 {request.tool} 吗？",
            detail=request.render(),
            choices=APPROVAL_CHOICES,
            title=request.tool,
        )

    def _interpret(self, request: ApprovalRequest, answer: Answer) -> Decision:
        key = answer.key
        if answer.cancelled:
            return Decision.refuse(request.level, "没有人回答，按拒绝处理")
        if key == YES:
            return Decision.allow(request.level, "确认放行")
        if key == ALWAYS and self._allow_always:
            self._session_allow.add(request.tool)
            return Decision.allow(request.level, "本会话内都允许")
        if key == NO:
            return Decision.refuse(request.level, "使用者拒绝了")
        return Decision.refuse(request.level, f"没有认出这个回答：{answer.text!r}")

    def _name(self, channel: object) -> str:
        return str(getattr(channel, "name", "?"))

    def _emit(self, event: str, *args: object) -> None:
        if self._events is not None:
            self._events.emit(event, *args)
