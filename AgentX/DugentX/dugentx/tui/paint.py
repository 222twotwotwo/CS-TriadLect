"""paint —— 把事件画成画面：TUI 与 harness 之间唯一的那层胶水。

**这层胶水本身就是「原样生长」的证据。** 它没有让循环知道界面存在，也没有让
界面去调循环的内部方法：它只是订阅了那些本来就有的事件，然后把它们翻译成一堆
渲染调用。换句话说，把这一整个文件删掉，harness 一行都不用改——循环照跑、
日志照记、工具照执行，只是没人看而已。

它同时驱动两条路：交互界面（`app.py`）和非交互输出（`plain.py`）。差别只在
「渲染调用最终落到哪儿」，**翻译规则一份**——所以「屏幕上看到的」和「管道里
拿到的」是同一份排版。

顺带做了两件编码 TUI 必须有、但 harness 不该关心的事：

- **改动前先拍快照。** `write_file` / `edit_file` 这类工具的结果是一句
  「已写入」，对人没有任何信息量；人要看的是 diff。所以这里在
  `tools/pre-execute` 上把文件原内容读出来存着，等结果到了再对比。
  为什么不去解析工具的输出？因为**输出是给模型看的**，它可能随时改写法；
  而在执行前读一遍文件，拿到的永远是事实。
- **状态条由事件推着走。** `model/switched`、`llm/usage`、`step/start`、
  `turn/end` 各自带着自己那半截事实，谁都不用去定时重画一次——
  一个每 200 毫秒刷新一次的界面，会让人分不清「变了」和「刷了一下」。

  唯一的例外是**转圈的帧和已经跑了多久**：那两个不是事实，是一个钟读出来的数，
  所以它们可以跳。界线画在这里：事件说「有活干了」（`turn/start`）和
  「活干完了」（`turn/end`），界面自己在那段时间里读钟。**没有任何一个事件是靠
  定时器猜出来的**，而定时器也只在有活的时候转——界面停下来的时候，它必须停。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from dugentx.kernel.context import Context
from dugentx.seams.messages import Delta, ToolCall
from dugentx.seams.tools import ToolOutcome
from dugentx.tui.formatting import NOTE_GLYPH, WRITE_TOOLS, Renderer, StatusUpdate


class RenderSwitch:
    """一次渲染调用的去向开关。

    画师在装载时就挂上了，可那时还不知道这一趟是交互还是 `--once`：前者要画到
    界面上，后者要写成整行文字。所以画师拿到的是这个开关，装载时默认指向纯文本，
    界面真正起来的时候再转过去（见 `app.py` 的 `on_mount` / `on_unmount`）。

    没有它就得挂两个画师、把事件订阅两遍——那两份订阅迟早会分叉。
    """

    def __init__(self, sink: Renderer) -> None:
        self._sink = sink

    @property
    def sink(self) -> Renderer:
        """此刻真正在收渲染调用的那一个。"""
        return self._sink

    def use(self, sink: Renderer) -> None:
        self._sink = sink

    def __getattr__(self, name: str) -> Any:
        # 转发。名字写错会在这里变成 AttributeError，而不是安静地什么都不做。
        return getattr(self._sink, name)


class EventPainter:
    """订阅事件，把 agent 正在做的事画出来。"""

    def __init__(
        self,
        ctx: Context,
        renderer: Renderer,
        *,
        status: Callable[[StatusUpdate], None] | None = None,
    ) -> None:
        self.ctx = ctx
        self.renderer = renderer
        self.status = status
        """状态条的收件人。非交互路径没有状态条，所以它是可选的——
        把「没有状态条」写成 `if` 一次，好过写一个什么都不做的方法。"""
        self._snapshots: dict[str, tuple[str, str]] = {}
        """call_id → (路径, 执行前的内容)。用 call_id 而不是路径做键：
        同一轮里两次改同一个文件是常见的事，按路径存会互相覆盖。"""

    # ---------------------------------------------------------------- 注册

    def attach(self) -> None:
        """把监听器挂上事件总线。全部走 `ctx.on`，所以卸载时一起消失。"""
        on = self.ctx.on
        on("turn/start", self._on_turn_start)
        on("step/start", self._on_step)
        on("llm/chunk", self._on_chunk)
        on("llm/usage", self._on_usage)
        on("step/end", self._on_step_end)
        on("tool/call", self._on_tool_call)
        on("permission/skip", self._on_permission_skip)
        on("permission/decided", self._on_permission_decided)
        on("model/switched", self._on_model_switched)
        on("plugin/mounted", self._on_plugin_mounted)
        on("plugin/unmounted", self._on_plugin_unmounted)
        on("skill/loaded", self._on_skill_loaded)
        on("agent/error", self._on_error)
        on("turn/end", self._on_turn_end)

        # 结果与 diff 走 post-execute 而不是 tool/result。原因很实际：
        # `emit` 是同步的，异步的读文件没法在里面被 await；而
        # `tools/post-execute` 是 waterfall，会被 await。
        # 顺带还拿到一个确定的顺序：先出结果行，再出 diff。
        #
        # 快照挂在 pre-execute 上，看一眼就走——它没有权力拦下任何调用，
        # 所以两个监听器都必须调 `nxt()`。
        on("tools/pre-execute", self._on_before_tool, prepend=True)
        on("tools/post-execute", self._on_after_tool)

    # ---------------------------------------------------------------- 事件

    def _on_turn_start(self, prompt: str) -> None:
        # 新回合从第 0 步开始：状态条上那个 steps 说的是**这个回合**走到了哪，
        # 不重置的话，上一个回合的数字会一直挂在那里，看着像这一轮已经跑了很多步。
        # 顺带说一句「有活了」：状态条从这里开始转圈、开始读表。
        self._status(StatusUpdate(steps=0, busy=True))

    def _on_step(self, index: int) -> None:
        step = int(index)
        self.renderer.step(step)
        # 每一步开始时再说一次「有活」：回合中间卡住的时候（比如审批在等人答），
        # 状态条仍然该是转着的——正在等一个人回答也是「这一步还没结束」。
        self._status(StatusUpdate(steps=step + 1, busy=True))

    def _on_chunk(self, delta: Delta) -> None:
        if delta.text:
            self.renderer.assistant_delta(delta.text)
        elif delta.reasoning:
            # 思考不往正文里塞：它是模型的自言自语，混进答案里会让人分不清
            # 哪句是结论。想看见它应该另开一个开关，而不是默认打开。
            return

    def _on_usage(self, usage: Any) -> None:
        self._status(StatusUpdate(tokens=self._tokens(usage)))

    def _on_step_end(self, index: int, tool_calls: int) -> None:
        if not tool_calls:
            self.renderer.assistant_end()

    def _on_model_switched(self, provider: str, model: str) -> None:
        self._status(StatusUpdate(model=self._model_label(provider, model)))

    def _on_tool_call(self, call: ToolCall) -> None:
        # 模型说完话才调工具，所以这里先把正文行收掉。
        self.renderer.assistant_end()
        self.renderer.tool_call(call.name, dict(call.arguments))

    async def _on_after_tool(self, outcome: ToolOutcome, nxt: Any) -> ToolOutcome:
        """工具跑完了：先出结果，再出 diff（如果有改动）。

        这个监听器**必须原样把 outcome 还回去**：它是渲染层，
        改了结果就等于悄悄篡改了模型看到的东西——那是 harness 能犯的
        最严重的一类错误之一。
        """
        self.renderer.tool_result(
            name=outcome.name,
            content=outcome.content,
            ok=outcome.ok,
            blocked=outcome.blocked,
        )
        await self._maybe_diff(outcome)
        return await nxt()

    def _on_permission_skip(self, request: Any) -> None:
        self.renderer.notice(f"{NOTE_GLYPH} {request.render()}（只读，直接放行）", kind="dim")

    def _on_permission_decided(self, request: Any, decision: Any) -> None:
        verdict = "同意" if decision.allowed else "拒绝"
        kind = "success" if decision.allowed else "warn"
        self.renderer.notice(f"{verdict}：{request.render()}", kind=kind)

    def _on_plugin_mounted(self, plugin_id: str, module: str) -> None:
        self.renderer.notice(f"挂上插件 {plugin_id}（{module}）", kind="success")

    def _on_plugin_unmounted(self, plugin_id: str) -> None:
        self.renderer.notice(f"拔掉插件 {plugin_id}", kind="dim")

    def _on_skill_loaded(self, name: str, tokens: int) -> None:
        self.renderer.notice(f"拉入技能 {name}（约 {tokens} tokens）", kind="dim")

    def _on_error(self, where: str, error: BaseException) -> None:
        self.renderer.error(f"{where}：{type(error).__name__}: {error}")

    def _on_turn_end(self, result: Any) -> None:
        self.renderer.assistant_end()
        self.renderer.turn_end(result)
        self._status(
            StatusUpdate(
                steps=int(getattr(result, "steps", 0)),
                tokens=self._tokens(result.usage),
                # 回合结束了，状态条停表。这是**唯一**能让它停下来的地方，
                # 而且它在循环里是 `finally` 里发的——一步炸掉也照样到这儿。
                busy=False,
            )
        )

    # ---------------------------------------------------------------- 状态条

    def _status(self, update: StatusUpdate) -> None:
        if self.status is not None:
            self.status(update)

    def _tokens(self, usage: Any) -> int:
        """累计 tokens。

        优先问会话日志：它是「这个会话一共花了多少」的唯一出处，每次都是重新
        算出来的实数。事件里带的那个数是**这一轮**的，拿它当累计值会让人以为
        花得很少——一个悄悄偏小的数字比没有数字更糟。读不到日志才退回事件里的数。
        """
        session = getattr(self.ctx.get("agent"), "session", None)
        if session is not None:
            try:
                return int(session.usage().total_tokens)
            except Exception:
                pass
        return int(getattr(usage, "total_tokens", 0) or 0)

    def _model_label(self, provider: str, model: str) -> str:
        """和 `ModelChoice.describe()` 同一套写法：没有 provider 就只写模型名。"""
        if provider and model:
            return f"{provider}/{model}"
        return str(model or provider or "")

    # ---------------------------------------------------------------- 快照

    async def _on_before_tool(self, call: ToolCall, nxt: Any) -> Any:
        """在执行前拍下文件原样。`nxt()` 必须调——这只是看一眼，不是拦路。"""
        if call.name in WRITE_TOOLS:
            snapshot = await self._snapshot(call)
            if snapshot is not None:
                self._snapshots[call.id] = snapshot
        return await nxt()

    async def _snapshot(self, call: ToolCall) -> tuple[str, str] | None:
        """读一读这个文件现在长什么样。拿不到就不做 diff。

        走 `ctx.fs` 而不是直接 `open()`：读的限额、路径策略、以及将来换成远端
        沙箱，都是那条缝保证过的事，这里不该绕过。
        """
        path = str(call.arguments.get("path") or "")
        fs = self.ctx.get("fs")
        if not path or fs is None:
            return None
        try:
            return path, await fs.read_text(path)
        except Exception:
            # 文件不存在（新建）或读不了（二进制、超限）——都不该让渲染层
            # 去干扰执行。新建文件的 diff 是「全部新增」，
            # 而 `tool_call` 那一行已经把路径说清楚了。
            return None

    async def _maybe_diff(self, outcome: ToolOutcome) -> None:
        snapshot = self._snapshots.pop(outcome.call_id, None)
        if snapshot is None or not outcome.ok:
            return
        path, before = snapshot
        fs = self.ctx.get("fs")
        if fs is None:
            return
        try:
            after = await fs.read_text(path)
        except Exception:
            return
        if after == before:
            return
        self.renderer.diff(path=path, before=before, after=after)


def attach_painter(
    ctx: Context,
    renderer: Renderer,
    *,
    status: Callable[[StatusUpdate], None] | None = None,
) -> EventPainter:
    """建一个画师并挂上事件。返回它，方便测试里直接驱动。"""
    painter = EventPainter(ctx, renderer, status=status)
    painter.attach()
    return painter
