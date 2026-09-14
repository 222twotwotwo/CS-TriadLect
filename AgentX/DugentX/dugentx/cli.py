"""命令行。

子命令刻意做得很少，因为**能被配置表达的东西不该再有专门的命令**：

    dugentx run "任务"      跑一个回合
    dugentx chat            多轮对话（同一个会话，日志持续追加）
    dugentx plugins         看这次到底装了哪些插件、按什么顺序
    dugentx events          看事件目录：哪些是通知，哪些是中间件
    dugentx sessions        列出本地会话日志
    dugentx replay <id>     把一个会话日志重新投影成模型看到的样子

最后一条是这个项目里最有用的一条命令：它把「模型可见 ⟺ 已记录」
从一句口号变成一个你随时能跑一下、看一眼的东西。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

import yaml

from dugentx import __version__
from dugentx.events import describe_events
from dugentx.kernel.errors import DuGentXError
from dugentx.runtime import DEFAULT_CONFIG, AgentRuntime
from dugentx.seams.agent import TurnResult

# ------------------------------------------------------------------ 输出


def _echo_event_names(runtime: AgentRuntime) -> list[str]:
    """挑几个事件打印出来，让一次运行看得见过程。

    只订阅少数几个——把每个 chunk 都打出来会把终端刷爆，
    而「看得见」和「刷屏」之间只差一点点克制。
    """
    watched = [
        "step/start",
        "tool/call",
        "tool/result",
        "permission/decided",
        "plugin/mounted",
        "plugin/unmounted",
    ]

    for name in watched:
        runtime.ctx.events.on(name, _make_printer(name))
    return watched


def _make_printer(name: str):  # type: ignore[no-untyped-def]
    def print_event(*args: Any) -> None:
        if name == "step/start":
            print(f"  · step {args[0] + 1}")
        elif name == "tool/call":
            call = args[0]
            print(f"    → {call.name}({call.arguments_json[:80]})")
        elif name == "tool/result":
            outcome = args[0]
            mark = "✓" if outcome.ok else ("⊘" if outcome.blocked else "✗")
            print(f"    {mark} {outcome.name}: {outcome.content.splitlines()[0][:90]}")
        elif name == "permission/decided":
            request, decision = args
            verdict = "同意" if decision.allowed else "拒绝"
            print(f"    ? {request.render()} → {verdict}（{decision.reason or request.level}）")
        elif name in ("plugin/mounted", "plugin/unmounted"):
            print(f"    ⚙ {name.split('/')[1]}：{args[0]}")

    return print_event


def _report(result: TurnResult) -> None:
    print()
    print(result.text or "（没有输出）")
    print()
    usage = result.usage
    print(
        f"[{result.stop_reason} · {result.steps} 步 · {result.tool_calls} 次工具调用 · "
        f"{usage.total_tokens} tokens]"
    )


# ------------------------------------------------------------------ 子命令


async def _cmd_run(args: argparse.Namespace) -> int:
    runtime = AgentRuntime.boot(args.config, cwd=Path.cwd())
    await runtime.start()
    try:
        _echo_event_names(runtime)
        result = await runtime.run(args.task)
        _report(result)
        return 0 if result.ok else 1
    finally:
        await runtime.stop()


async def _cmd_chat(args: argparse.Namespace) -> int:
    runtime = AgentRuntime.boot(args.config, cwd=Path.cwd())
    await runtime.start()
    try:
        agent = runtime.agent
        print(f"会话 {agent.session.session_id}；空行或 Ctrl-D 结束。")
        while True:
            try:
                line = await asyncio.to_thread(input, "\n你 > ")
            except EOFError:
                break
            if not line.strip():
                break
            result = await agent.send(line)
            _report(result)
        return 0
    finally:
        await runtime.stop()


async def _cmd_plugins(args: argparse.Namespace) -> int:
    """真的把插件装一遍再打印。

    只是「读配置算一下顺序」是不够的：这张表的用处是回答
    「现在到底有什么能用」。装一遍才知道——而且如果装不上，
    那个错误本身就是你要看的东西。
    """
    runtime = AgentRuntime.boot(args.config, cwd=Path.cwd())
    await runtime.start()
    try:
        print(runtime.tree())
    finally:
        await runtime.stop()
    return 0


async def _cmd_replay(args: argparse.Namespace) -> int:
    """把一个会话日志重新投影成模型看到的样子。

    这条命令的存在本身就是一种设计声明：上下文不是存在别处的状态，
    它是日志的一个函数。你能把任意一个历史会话按当时的规则重新算一遍。
    """
    from dugentx.seams.session import JsonlSessionStore, derive_messages, derive_view

    runtime = AgentRuntime.boot(args.config, cwd=Path.cwd())
    await runtime.start()
    try:
        store: JsonlSessionStore = runtime.ctx.service("sessionStore")
        log = store.load(args.session_id)
    finally:
        await runtime.stop()

    print(f"会话 {log.session_id}：{len(log)} 个事件")
    print("\n=== 日志 ===")
    for event in log:
        payload = ", ".join(f"{k}={_short(v)}" for k, v in event.data.items())
        print(f"{event.seq:>4}  {event.kind:<20} {payload}")

    print("\n=== 完整投影（derive_messages）===")
    for message in derive_messages(log):
        print("  " + message.text_of(120))

    view = derive_view(log)
    if len(view) != len(derive_messages(log)):
        print("\n=== 实际发给模型（derive_view，已套用压缩）===")
        for message in view:
            print("  " + message.text_of(120))
    return 0


def _cmd_events(_args: argparse.Namespace) -> int:
    print("派发方式   事件名                  说明")
    print("-" * 78)
    print(describe_events())
    return 0


def _session_directory(config_path: str | None) -> Path:
    """从配置里读出会话目录，**不装载任何插件**。

    `dugentx sessions` 是只读命令，它不该在磁盘上留下东西。
    走一次完整的 boot 会顺手建一个新会话、再落一个空文件——
    列一次目录就多一条记录，这种副作用比命令本身更难查。

    没有配置文件时退回默认值，和 session 插件的默认值保持一致。
    """
    path = Path(config_path) if config_path else Path(DEFAULT_CONFIG)
    if not path.exists():
        return Path(".dugentx/sessions")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for row in data.get("plugins") or []:
        if isinstance(row, dict) and str(row.get("plugin", "")).endswith("plugins.session"):
            return Path(str((row.get("config") or {}).get("directory", ".dugentx/sessions")))
    return Path(".dugentx/sessions")


def _cmd_sessions(args: argparse.Namespace) -> int:
    directory = _session_directory(args.config)
    if not directory.exists():
        return 0
    for path in sorted(directory.glob("*.jsonl")):
        size = path.stat().st_size
        print(f"{path.stem}  {size:>8}B")
    return 0


def _short(value: Any, limit: int = 70) -> str:
    text = str(value).replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ------------------------------------------------------------------ 入口


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dugentx",
        description="DugentX —— 一个可拔插的 Agent harness（LLM 层用 any-llm，其余自造）",
    )
    parser.add_argument("--version", action="version", version=f"dugentx {__version__}")
    parser.add_argument("-c", "--config", default=None, help="组合配置，默认 ./dugentx.yml")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="跑一个回合")
    run.add_argument("task", help="要它做的事")
    run.set_defaults(func=_cmd_run)

    chat = sub.add_parser("chat", help="多轮对话")
    chat.set_defaults(func=_cmd_chat)

    plugins = sub.add_parser("plugins", help="看这次装了哪些插件")
    plugins.set_defaults(func=_cmd_plugins)

    replay = sub.add_parser("replay", help="把一个会话日志重新投影一遍")
    replay.add_argument("session_id")
    replay.set_defaults(func=_cmd_replay)

    events = sub.add_parser("events", help="事件目录")
    events.set_defaults(func=_cmd_events)

    sessions = sub.add_parser("sessions", help="列出本地会话")
    sessions.set_defaults(func=_cmd_sessions)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if asyncio.iscoroutinefunction(args.func):
            return int(asyncio.run(args.func(args)))
        return int(args.func(args))
    except DuGentXError as exc:
        print(f"dugentx: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print()
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
