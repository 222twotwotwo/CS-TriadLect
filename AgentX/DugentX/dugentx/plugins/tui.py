"""tui 插件 —— 把编码 TUI 当成 human 通道挂上去。

这个文件是整件事的答案。加一个 TUI 需要做的事只有这一件：

> **提供一个 `ctx.human`。**

没有改循环、没有给工具加分支、没有让审批知道界面的存在。
把 `dugentx.yml` 里的 `human` 那行换成 `tui` 这行，终端里就长出一个
编码 TUI——审批弹在它该在的地方，工具调用带着 diff 显示，
模型流式吐字的时候你一个字一个字看得见。

它和 `plugins/human.py` 提供**同一个服务键**，所以一份配置里只能有一个。
这不是限制，而是这条缝的设计：一个进程里同时有两张嘴问同一个人，
只会把问题问乱。要换回来，改一行配置。

它**不做**的四件事，每件都是刻意的：

- 不接管 `agent.send`。回合还是循环在跑，TUI 只是一个观察者 + 一个输入源。
- 不改工具结果。`tools/post-execute` 上的画师必须原样返回 outcome。
- 不假设自己在终端里。不是 TTY 时它照样装得起来（`interactive()` 为假），
  于是审批会拒绝而不是挂住——CI 里跑同一个配置不会卡死。
- **不假设 textual 装好了。** 它不在就换纯文本那条路：`--once` 照样能跑，
  只有交互模式会停下来，用一行话说清楚装什么（`uv sync --extra tui`）。
  把「装没装界面库」变成一段 ImportError traceback，是这一层最不该犯的错。
"""

from __future__ import annotations

import sys
from typing import Any

from dugentx.kernel.context import Context
from dugentx.kernel.errors import PluginError
from dugentx.kernel.plugin import define_plugin
from dugentx.tui.paint import attach_painter
from dugentx.tui.plain import PlainTui, TuiConfig, is_tty

_KNOWN = {"prompt", "show_banner", "history_limit", "animate"}


@define_plugin(
    "tui",
    inject=("agent",),
    provides=("human", "tui"),
    description="编码 TUI：既是一个 human 通道，也是一个可运行的界面（textual 可选）",
)
def create(ctx: Context, config: dict[str, Any] | None = None) -> None:
    """装配界面、挂上画师、把 `ctx.human` 换成 TUI 通道。"""
    settings = dict(config or {})
    unknown = set(settings) - _KNOWN
    if unknown:
        raise PluginError(
            f"tui 配置里有不认识的键：{sorted(unknown)}；可用：{sorted(_KNOWN)}"
        )

    tui_config = TuiConfig(
        prompt=str(settings.get("prompt", TuiConfig.prompt)),
        show_banner=bool(settings.get("show_banner", True)),
        history_limit=int(settings.get("history_limit", TuiConfig.history_limit)),
        animate=bool(settings.get("animate", True)),
    )

    app = build(ctx, tui_config, tty=is_tty(sys.stdin))
    # 画师只管看：它订阅的事件本来就有，删掉这个文件 harness 照样跑。
    # 状态条只挂在界面那一侧——非交互路径没有状态条，它的 `status_sink` 就是 None。
    attach_painter(ctx, app.renderer, status=app.status_sink)

    ctx.provide("human", app.human)
    ctx.provide("tui", app)


def build(ctx: Context, config: TuiConfig, *, tty: bool) -> Any:
    """有 textual 就用界面，没有就用纯文本那条路。

    导入放在函数里，是这里唯一重要的细节：配置里写了 tui、但没装 `--extra tui`
    的机器上，`dugentx plugins` / `dugentx run` 仍然该能跑。所以缺依赖时**不是
    装载失败**，而是换一个实现——它提供同样的两个服务键（`human` 和 `tui`），
    只是交互模式起不来；那时候由 `PlainTui.run` 用一行话说清楚该装什么。
    """
    try:
        from dugentx.tui.app import TuiApp
    except ImportError:
        plain = PlainTui(ctx, config=config)
        return plain
    return TuiApp(ctx, config=config, tty=tty)
