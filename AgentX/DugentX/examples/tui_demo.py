"""无 key 看一遍编码 TUI：真工具、真 diff、真权限，只有模型是假的。

    uv run python examples/tui_demo.py

它读的是 `dugentx.tui.yml`——和你在终端里 `dugentx tui` 用的是同一份配置，
只改了一处：把 llm 那一行换成回放适配器。所以这里看到的**内容**，
就是真跑起来时的内容。

它走的是 `--once` 那条路：整行文字，不需要 textual。理由很实际——
一个需要 API key、还需要真终端才能看一眼的界面，等于把大部分想读它的人
挡在门外。界面本身要真坐在终端前才起来（`dugentx tui`，或 /provider
命令行里的 Ctrl-O）。排版两边是同一份（`dugentx/tui/formatting.py`），
所以这里看到的每一步，界面上长得一样。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dugentx.runtime import AgentRuntime  # noqa: E402

TARGET = ROOT / "examples" / "workspace" / "demo-note.txt"

SCRIPT = [
    [
        {"reasoning": "先看看那个文件里现在写了什么。"},
        {"text": "我先读一下这个文件。\n"},
        {"tool_call": "read_file", "arguments": {"path": "examples/workspace/demo-note.txt"}},
    ],
    [
        {"text": "读到了，是一行待办。我把它改成完成状态。\n"},
        {
            "tool_call": "edit_file",
            "arguments": {
                "path": "examples/workspace/demo-note.txt",
                "old": "状态：待办",
                "new": "状态：完成",
            },
        },
    ],
    [{"text": "改完了。diff 在上面——你看到的就是这个 TUI 存在的理由。\n"}],
]


async def main() -> int:
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text("# 演示用的一行笔记\n状态：待办\n", encoding="utf-8")

    runtime = AgentRuntime.boot(
        ROOT / "dugentx.tui.yml",
        cwd=ROOT,
        overrides=[
            {
                "id": "llm",
                "config": {
                    "adapter": "replay",
                    "model": "replay",
                    "replay": {"on_exhausted": "error", "turns": SCRIPT},
                },
            },
            # 演示要一口气跑完，所以把「问人」换成「自动放行」。
            # 真跑的时候这里留着 mode: channel，它会停下来问你一句。
            {
                "id": "permissions",
                "config": {"mode": "policy", "answers": {"confirm": "allow"}},
            },
        ],
    )

    await runtime.start()
    try:
        app = runtime.ctx.service("tui")
        return await app.run(once="把 demo-note.txt 里的状态改成完成")
    finally:
        await runtime.stop()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
