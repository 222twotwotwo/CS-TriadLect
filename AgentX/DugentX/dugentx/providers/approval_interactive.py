"""交互式审批 —— 有人坐在终端前时的那个回答者。

三个决定，每一个都是为了不让人被这套机制训练成「无脑按 y」：

1. **问之前先看有没有人。** stdin 不是终端（CI、管道、输出被重定向）就
   一律拒绝，并说明原因。一个在 CI 里等输入的 harness，比一个直接说「不」的
   harness 糟糕得多：前者占着资源直到有人发现，而后者把问题写在了日志里。
2. **`a` 记住一整个会话。** 「这个工具别再问了」是最常见的一种回答。
   每次都要再按一次 y，只会训练出不停按 y 的人，而那就等于没有审批。
3. **提问走 `asyncio.to_thread(input, ...)`。** `input()` 是阻塞的，
   直接在协程里调它会把整个事件循环连同别的任务一起冻住——
   审批本来只该挡住这一次工具调用。

提问和「有没有终端」这两件事都做成可注入的，不是为了灵活，而是为了让
审批逻辑可测：一个只能在真终端上测的审批路径，等于一条没测过的路径。
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable
from typing import IO

from dugentx.seams.permissions import ApprovalRequest, Decision


class InteractiveApproval:
    """在终端上问 y / n / a，并记住本会话里放行过的工具。"""

    def __init__(
        self,
        *,
        stream: IO[str] | None = None,
        ask: Callable[[str], str] | None = None,
    ) -> None:
        """建一个交互式审批者。

        `stream` 只用来判断「stdin 是不是终端」，默认 `sys.stdin`。
        `ask` 是提问函数，默认内建 `input`；测试里可以换成直接给答案的假函数。
        """
        self._stream: IO[str] | None = stream if stream is not None else sys.stdin
        self._ask = ask if ask is not None else input
        self._session_allow: set[str] = set()

    @property
    def allowed_tools(self) -> frozenset[str]:
        """本会话里被 `a` 整体放行过的工具名。"""
        return frozenset(self._session_allow)

    async def decide(self, request: ApprovalRequest) -> Decision:
        """问一次。永远不会阻塞在没有人的环境里。"""
        if request.tool in self._session_allow:
            return Decision.allow(request.level, f"{request.tool} 已在本会话里整体放行过")

        if not self._is_interactive():
            return Decision.refuse(
                request.level,
                "标准输入不是终端，没有人能回答这次审批；"
                "要无人值守请把 permissions 插件的 mode 设成 policy",
            )

        prompt = (
            f"\n需要批准：{request.render()}\n"
            f"  级别：{request.level}（标签：{'、'.join(sorted(request.labels)) or '无'}）\n"
            f"  [y] 允许  [n] 拒绝  [a] 本会话里 {request.tool} 全部允许 > "
        )
        try:
            answer = await asyncio.to_thread(self._ask, prompt)
        except (EOFError, KeyboardInterrupt):
            # 输入流结束或人按了 Ctrl-C：都当成「不批准」。
            # 这里绝不能重试——重试就是在无人看管的终端上永远等下去。
            return Decision.refuse(request.level, "没有读到答复（输入结束或被打断），按拒绝处理")

        token = answer.strip().lower()
        if token.startswith("a"):
            self._session_allow.add(request.tool)
            return Decision.allow(request.level, f"你按了 a：{request.tool} 在本会话里不再询问")
        if token.startswith("y"):
            return Decision.allow(request.level, "你按了 y")
        return Decision.refuse(request.level, f"收到的答复是 {answer.strip() or '空'}，视为拒绝")

    def _is_interactive(self) -> bool:
        """stdin 是不是一个真的终端。

        `isatty()` 在不同环境里会抛不同异常（伪文件对象没有这个方法，
        已关闭的流抛 ValueError）。三种情况一律当成「不是终端」：
        判断不出来的时候，拒绝是安全的那个答案。
        """
        try:
            stream = self._stream
            return bool(stream is not None and stream.isatty())
        except (AttributeError, ValueError, OSError):
            return False
