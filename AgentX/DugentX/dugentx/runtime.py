"""开机：把一份组合变成一棵装好的插件树，然后交给你一个 agent。

这一层只做四件事，多一件都不做：

1. 建一个根上下文，并把**事件目录**交给它的事件总线（写错事件名会当场报错）；
2. 按装载器排好的顺序 mount 每个插件；
3. 把 `agent` 服务取出来，作为默认的对话对象；
4. `stop()` 时按相反顺序撤销一切。

第 1 条值得单独说：`EventBus(EVENT_MODES)` 这个参数让「事件名」从约定
变成契约。没有它，一个拼错的事件名会安静地什么都不做——这是最难查的
一类 bug，因为代码看起来完全正常。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dugentx.events import EVENT_MODES
from dugentx.kernel.context import Context
from dugentx.kernel.effect import Disposer
from dugentx.kernel.events import EventBus
from dugentx.kernel.loader import Composition, PluginRow, load_composition
from dugentx.seams.agent import Agent, TurnResult

DEFAULT_CONFIG = "dugentx.yml"


class AgentRuntime:
    """一次运行的完整状态：上下文 + 装好的插件 + 主 agent。"""

    def __init__(self, composition: Composition, *, cwd: Path | None = None) -> None:
        self.composition = composition
        self.cwd = (cwd or Path.cwd()).resolve()
        self.ctx = Context("root", events=EventBus(EVENT_MODES))
        self._started = False

    # ---------------------------------------------------------------- 启动

    @classmethod
    def boot(
        cls,
        config_path: str | Path | None = None,
        *,
        overrides: list[dict[str, Any]] | None = None,
        cwd: Path | None = None,
    ) -> AgentRuntime:
        """读配置、排顺序、建上下文。**还没有装载任何插件。**"""
        path = config_path
        if path is None:
            candidate = Path(cwd or Path.cwd()) / DEFAULT_CONFIG
            path = candidate if candidate.exists() else None
        composition = load_composition(path, overrides=overrides)
        return cls(composition, cwd=cwd)

    async def start(self) -> AgentRuntime:
        """按顺序装载。任何一个插件装不上，就整体失败——不留半个运行时。"""
        if self._started:
            return self
        mounted: list[Disposer] = []
        try:
            for plugin in self.composition.plugins:
                mounted.append(await self.ctx.mount(plugin))
        except BaseException:
            for dispose in reversed(mounted):
                dispose()
            raise
        self._started = True
        return self

    async def stop(self) -> None:
        """撤销整棵树。"""
        self.ctx.dispose()
        self._started = False

    async def __aenter__(self) -> AgentRuntime:
        return await self.start()

    async def __aexit__(self, *exc: Any) -> None:
        await self.stop()

    # ---------------------------------------------------------------- 使用

    @property
    def agent(self) -> Agent:
        return self.ctx.service("agent")  # type: ignore[no-any-return]

    async def run(self, prompt: str) -> TurnResult:
        return await self.agent.send(prompt)

    def services(self) -> dict[str, Any]:
        return self.ctx.services()

    def tree(self) -> str:
        """打印插件树——`dugentx plugins` 命令用它。

        一个可拔插系统的第一件诊断工具，就是「现在到底装了些什么」。
        """
        lines = [f"配置：{self.composition.source}", f"工作目录：{self.cwd}", "", "按装载顺序："]
        lines.append(self.composition.describe())
        lines.append("")
        lines.append("可用服务：" + "、".join(sorted(self.services())))
        return "\n".join(lines)

    def rows(self) -> list[PluginRow]:
        return list(self.composition.rows)
