"""shell 工具 —— 把 `ctx.shell` 交给模型。

只有一条命令工具，是刻意的。这里要回答一个设计问题：`run_command` 该不该
带 `LABEL_DANGEROUS`？

标签是**构造时**固定的，而「这条命令危不危险」取决于命令文本，构造期根本
看不到。所以两个选项：

1. 注册两个工具（`run_command` 和 `run_dangerous_command`）——不做。
   工具是模型自己挑的，给它一个「危险版」不是策略，是邀请：它想绕开约束时
   会直接调那个，而权限分级的本意是让它没有这个选项。
2. 让能看到命令文本的那一层去判定——做这个。有两个位置能看到：
   `ShellPolicy.check`（shell 插件配置的 `deny_substrings` / `allow_prefixes`）
   硬拒，永远生效，并且把「为什么拦」写进返回里；以及审批层，
   `ApprovalRequest.arguments` 里就带着 command，`PolicyApproval` 的
   `argument_rules` 可以按文本拒掉特定命令。

所以：`run_command` 打 `LABEL_WRITE`（要问一句），要按文本拦危险命令就配
`argument_rules` 或 `deny_substrings`。如果整台机器上的部署决定是
「任何 shell 命令都要按最严那档走」，那是配置决定，本插件配置里的
`dangerous: true` 会把 `LABEL_DANGEROUS` 加到这条工具上。
"""

from __future__ import annotations

from typing import Any

from dugentx.kernel.context import Context
from dugentx.kernel.effect import Disposer
from dugentx.kernel.plugin import define_plugin
from dugentx.seams.tools import LABEL_DANGEROUS, LABEL_WRITE, tool_from_function


async def run_command(ctx: Context, command: str, timeout: float | None = None) -> str:
    """在工作区目录里执行一条命令，返回它的输出。

    命令走一个真实的 shell，所以管道、重定向和 `&&` 都能用。
    有超时：到点会杀掉整棵进程树，并把「超时」写成结果的一部分——
    它不会一直等下去，你也不必为了怕它卡住而不敢跑长命令。
    输出过长会被截断，截断的位置有明确说明。

    Args:
        command: 要执行的完整命令行文本
        timeout: 最多等多少秒；不传就用 shell 插件配置里的默认值
    """
    result = await ctx.service("shell").run(command, timeout=timeout)
    return result.render()


def register(ctx: Context, *, dangerous: bool = False) -> Disposer:
    """注册命令工具，返回撤掉它的 disposer。

    `dangerous=True` 时给这条工具加上 `LABEL_DANGEROUS`——默认策略里那一档是
    直接拒绝，所以这是「所有 shell 命令都要显式放行」的部署开关，
    不是给单条命令用的：单条命令的判定看命令文本，理由写在模块头部。
    """
    labels = frozenset({LABEL_WRITE, LABEL_DANGEROUS} if dangerous else {LABEL_WRITE})
    tool = tool_from_function(run_command, labels=labels)
    return ctx.service("tools").register(tool)


@define_plugin(
    "shell_tools",
    inject=("tools", "shell"),
    description="命令工具：run_command（一个工具，危险判定按命令文本走审批层）",
)
def create(ctx: Context, config: dict[str, Any]) -> None:
    """装载命令工具。

    配置只有一项：`dangerous`（默认 false），把整条工具提到 `dangerous` 档。
    """
    dangerous = bool(config.get("dangerous", False))
    ctx.effect(lambda c: register(c, dangerous=dangerous), label="tools:shell")
