"""编码 TUI（textual）。

它不是 harness 里的一层，而是 human 缝上的一个实现：`ctx.human` 由
`dugentx/plugins/tui.py` 提供，审批与「问人」走的还是那条本来就在的通道，
harness 一行没改。

分工：

- `formatting.py`  纯排版：一件事该长成哪几行；外加非交互路径的渲染器
- `commands.py`    输入框里那些 `/` 命令（一张表，帮助、命令面板、补全都读它）
- `fuzzy.py`       名字的模糊匹配：补全弹出和 provider 选择器共用一套「像不像」
- `paint.py`       订阅事件，把它们翻译成排版调用（两条路共用）
- `plain.py`       非交互那条路：`--once`、CI、textual 不在时的兜底
- `widgets.py`     状态条、补全弹出、provider 选择器、审批弹窗
- `app.py`         textual 应用本身（也在这里实现 `ctx.human`）

**这个文件故意不 import textual。** textual 是可选依赖（`--extra tui`），
而 `dugentx run` / `plugins` 不欠界面任何东西：它们用不到这个包；真要用到的时候
（`plugins/tui.py`）会在能接住 ImportError 的地方再导，缺了就换纯文本那条路，
而不是抛一段 traceback。
"""

from dugentx.tui.commands import COMMANDS, SlashCommand, help_lines, split
from dugentx.tui.formatting import (
    PlainRenderer,
    Renderer,
    StatusUpdate,
    summarize_arguments,
    unified_diff,
)
from dugentx.tui.plain import PlainTui, TuiConfig, is_tty, runtime_facts

__all__ = [
    "COMMANDS",
    "PlainRenderer",
    "PlainTui",
    "Renderer",
    "SlashCommand",
    "StatusUpdate",
    "TuiConfig",
    "help_lines",
    "is_tty",
    "runtime_facts",
    "split",
    "summarize_arguments",
    "unified_diff",
]
