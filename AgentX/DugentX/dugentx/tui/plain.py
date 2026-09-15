"""plain —— 不依赖 textual 的那条路。

`--once`、CI、`examples/tui_demo.py` 走这里：整行输出，没有界面。textual 不在
的时候，它也是整个 TUI 的兜底——照样提供 `ctx.human` 和 `ctx.tui`，只是交互
模式起不来；那种情况它会用一行话说清楚该装什么，而不是抛一段 ImportError。

**为什么非交互这条路必须单独存在。** 一个只能在真终端、或者只能在装了 UI 依赖
时才能跑的东西，等于把 CI 和脚本挡在门外。TUI 的价值在界面，但「跑一个回合」
不欠界面任何东西：排版在 `formatting.py`，事件翻译在 `paint.py`，这两样都不认识
textual，所以少一个依赖就少一层界面，不会少一次执行。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import IO, Any

from dugentx.kernel.context import Context
from dugentx.seams.human import Answer, NoteKind, Question
from dugentx.tui.formatting import PlainRenderer, Renderer

TEXTUAL_HINT = "dugentx: 这个 TUI 需要 textual，装一下就能用：uv sync --extra tui"
"""textual 不在时交互模式唯一要说的话。

只有一行，因为这里没有别的话值得说：缺什么、怎么补，都在这一句里。
（不想装也能用：`dugentx tui --once "..."` 走的是纯文本那条路。）"""


@dataclass
class TuiConfig:
    """TUI 的配置。每一项都对应一个真的会变的决定。"""

    prompt: str = "› "
    show_banner: bool = True
    history_limit: int = 200
    """记住多少条历史输入。够了就行——这是给人按上箭头用的，不是审计日志。"""
    animate: bool = True
    """状态条上那个转圈和跟着跳的秒数。

    关掉之后状态条在干活时显示一个**静止**的字形（`formatting.SPINNER_STATIC`），
    不转也不读表。非交互那条路本来就没有动画——它连状态条都没有，往管道里写
    动画只是把日志弄脏。这一项是给「在真终端里也不想看见动的东西」的人留的
    （录屏、回放、单纯不喜欢）。"""


def is_tty(stream: IO[str] | None = None) -> bool:
    """这个流是不是连着终端。判断不出来时一律返回 False。

    `isatty` 在不同环境里抛不同异常（伪文件对象根本没有这个方法，已经关掉的流
    抛 ValueError）。这些情况全部按「不是终端」处理：猜错成「是」的代价更大——
    应用会去等一个永远不会来的按键，而人是看不到这个等待的。
    """
    target = sys.stdin if stream is None else stream
    try:
        return bool(target.isatty())
    except (AttributeError, ValueError, OSError):
        return False


def runtime_facts(ctx: Context) -> dict[str, Any]:
    """开场那几行要的事实：现在用哪个模型、哪个会话、在哪个目录、几件工具。

    全部走 `ctx.get`：读不到就是空。界面不该因为某个服务不在就起不来，
    它只该说「这一项还不知道」——那正是 `（未指定）` 存在的理由。
    """
    agent = ctx.get("agent")
    session = getattr(agent, "session", None)
    config = getattr(agent, "config", None)

    model = ""
    models = ctx.get("models")
    if models is not None:
        try:
            model = str(models.current().describe())
        except Exception:
            # 换模型的能力是可选的（没有 llm 插件就没有 ctx.models）。
            # 缺了它只是少一行信息，不是起不来的理由。
            model = ""
    if not model:
        model = str(getattr(config, "model", "") or "")

    fs = ctx.get("fs")
    tools = ctx.get("tools")
    return {
        "model": model,
        "session_id": str(getattr(session, "session_id", "") or ""),
        "cwd": str(getattr(fs, "root", "") or ""),
        "tools": len(tools.names()) if tools is not None else 0,
    }


class PlainHuman:
    """没有界面时的 human 通道：只有单向的 `note`，问不了人。

    `interactive()` 恒为 False，所以 `ask` / `choose` 直接返回「取消」——审批
    据此**拒绝**而不是挂住。代价写在明处：这种环境里该问的都会被拒掉。
    想无人值守地放行，答案该写进 `permissions` 的配置里——那是策略的事，
    不是界面该猜的事。
    """

    name = "tui"

    def __init__(self, renderer: Renderer) -> None:
        self._renderer = renderer

    def interactive(self) -> bool:
        return False

    def note(self, text: str, *, kind: NoteKind = "info") -> None:
        if text:
            self._renderer.notice(text, kind=kind)

    async def ask(self, question: Question) -> Answer:
        return Answer(cancelled=True, source=self.name)

    async def choose(self, question: Question) -> Answer:
        return Answer(cancelled=True, source=self.name)


class PlainTui:
    """非交互模式：跑一个回合，把过程画成整行文字。

    它是 `ctx.tui` 在没有 textual 时的形态。`run(once=None)` 是唯一的
    「我不该在这里」分支：交互模式需要界面，而界面没装——这时候唯一有用的
    事情是把缺什么说清楚，退出码非零。
    """

    def __init__(
        self,
        ctx: Context,
        *,
        config: TuiConfig | None = None,
        stream: IO[str] | None = None,
    ) -> None:
        self.ctx = ctx
        self.config = config or TuiConfig()
        self.renderer = PlainRenderer(stream)
        self._human = PlainHuman(self.renderer)

    @property
    def human(self) -> PlainHuman:
        return self._human

    @property
    def status_sink(self) -> None:
        """这条路没有状态条，所以没有收件人。

        写成显式的一个 `None`，而不是给 `PlainRenderer` 加一个什么都不做的
        `status()`：一个安静地不生效的方法，谁都不会发现它其实没生效。
        """
        return None

    async def run(self, *, once: str | None = None) -> int:
        """`--once` 跑一个回合；没有 `--once` 就说明交互模式要不了。"""
        if once is None:
            print(TEXTUAL_HINT, file=sys.stderr)
            return 2
        return await self.turn(once)

    async def turn(self, text: str) -> int:
        """跑一个回合。退出码有意区分：0 = 跑完了，1 = 这个回合炸了，2 = 起不来。

        一个「炸了却返回 0」的命令，在脚本里是最难查的那种失败——它什么都不说。
        """
        agent = self.ctx.get("agent")
        if agent is None:
            self.renderer.error("这次组合里没有 agent，跑不了")
            return 2
        if self.config.show_banner:
            self.renderer.banner(**runtime_facts(self.ctx))
        self.renderer.user(text)
        try:
            await agent.send(text)
        except Exception as exc:  # 一个回合炸掉不该变成一段 traceback
            self.renderer.error(f"{type(exc).__name__}: {exc}")
            return 1
        return 0
