"""session 插件 —— 会话日志的载体，和它的磁盘副本。

这个插件只做两件事：

1. 建一个 `JsonlSessionStore`，按配置的 `session_id` 决定「接着上次」还是「开新的」，
   然后把 store 和当前这份 `SessionLog` 放进上下文（`sessionStore` / `session`）。
2. 在**回合边界**把日志刷到盘上。

第 2 点有一处刻意的克制，值得写下来，免得后来的人「顺手补充」：

> **它只落盘，不记录。** 日志里有什么，是拥有那件事的代码写的——
> 循环写 `user/message`、`assistant/message`、`tool/result`、`step/*`、`system/message`，
> 权限和插件各自写自己那几条。这个插件一个字段都不补。

为什么这么严？因为「谁拥有事实」一旦不唯一，日志里就会出现两条说同一件事、
数据却略有出入的事件；而 `dugentx replay` 会老老实实把两条都放回模型面前。
那种 bug 不会有异常，只会有「模型怎么突然重复了一遍」。所以规矩是：
**写日志的永远是拥有那件事的代码；监听器只观察，不记录。**

于是这里订阅的事件只有一个用途：**知道「现在值得存一次」**。
不用定时器、不用退出钩子，是因为这两样在 harness 崩掉时都不一定跑得到；
而回合边界一定有人经过——模型答完了一轮，日志就该完整地躺在磁盘上。
`SessionLog` 本身是纯内存的、只能追加的值对象，把持久化留在外面，
换存储（SQLite、远端）时一行上层代码都不用动。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dugentx.kernel.context import Context
from dugentx.kernel.plugin import define_plugin
from dugentx.seams.session import JsonlSessionStore, SessionLog

DEFAULT_DIRECTORY = Path(".dugentx") / "sessions"
"""默认放在工作目录下的隐藏目录里：会话日志是工作区的一部分，不是全局状态。"""

FLUSH_ON: tuple[str, ...] = ("turn/end",)
"""值得写盘的时刻。

只挑回合边界，不挑每一个事件：一次 `save()` 是重写整个文件，粒度太细会让
长会话白白多写几十兆；粒度太粗（只在退出时写）又会让崩溃带走一整段对话。
回合结束是「一段完整的东西刚刚落定」的那个点——**主会话的每一轮对话都必然
以 `turn/end` 收尾**，所以这个清单只要它一个就够了。

两个**故意不在**清单里的名字：

- `session/start`：按启动就写盘的话，一条只想读东西的命令（`dugentx plugins`、
  `dugentx sessions`）每启动一次就会留下一个空会话文件——「我有几个会话」于是
  变成一个越问越错的数字。所以下面的 flush 还带一道闸：**没有 `turn/start` 就不写**。
- `session/end`：它是**会话日志的种类**，不是总线事件（见 `dugentx/events.py`
  里生命周期那一段）。总线根本不派发它，写在这里等于挂一个永远不触发的监听器；
  而「收尾那一次」由下面的卸载 flush 负责。

什么都没干的一次装载，不该在磁盘上留下痕迹。
"""


@define_plugin(
    "session",
    provides=("session", "sessionStore"),
    description="会话日志（可恢复）+ JSONL 持久化：回合结束时把日志刷到磁盘",
)
def create(ctx: Context, config: dict[str, Any] | None = None) -> None:
    """装载会话层。

    `directory` 给相对路径时按**进程的工作目录**解析——工作目录是「这次运行的
    工作区」这个事实的载体，配置里的相对路径应该跟着它走，而不是跟着配置文件的位置。
    """
    settings = dict(config or {})
    store = JsonlSessionStore(_resolve_directory(settings.get("directory")))
    session_id = str(settings["session_id"]).strip() if settings.get("session_id") else None

    if session_id and store.path_for(session_id).exists():
        # 恢复：日志就是上下文的唯一真相，读回来即可，不需要再问模型一遍。
        log = store.load(session_id)
    else:
        log = SessionLog(session_id)

    ctx.provide("sessionStore", store)
    ctx.provide("session", log)

    worked = False
    """这次装载之后，日志里有没有真的出现过一轮对话。"""

    def flush(*_payload: Any) -> None:
        """事件监听器：不看参数，只是「把日志写下来」这个信号。"""
        nonlocal worked
        # 判据只看一次就够：日志只能追加，一旦有 turn/start 就永远是 True。
        if not worked:
            worked = log.count("turn/start") > 0
        if not worked:
            return
        store.save(log)

    for event in FLUSH_ON:
        ctx.on(event, flush)

    # 卸载时再存一次：插件被拔掉的地方，往往正是运行结束的地方（`runtime.stop()`）。
    ctx.effect(lambda _ctx: lambda: flush(), label="session:flush-on-unmount")


def _resolve_directory(value: Any) -> Path:
    if value is None or value == "":
        return (Path.cwd() / DEFAULT_DIRECTORY).resolve()
    path = Path(str(value)).expanduser()
    return path.resolve() if path.is_absolute() else (Path.cwd() / path).resolve()
