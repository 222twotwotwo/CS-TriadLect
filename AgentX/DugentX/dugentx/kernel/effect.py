"""可撤销的副作用。

这是整套架构里最不起眼、但最关键的一条规则：

> **每一笔注册都是一个 effect，返回一个 disposer；插件卸载时按相反顺序撤销。**

没有这条，插件就只是「启动时跑一次的代码」，谈不上可拔插——
拔下来的时候，它留下的服务、监听器、工具会继续活着，变成幽灵。
有了这条，`mount` 和 `unmount` 才是真的对称操作，
运行期动态挂载（`plugin` 工具）才可能是对的。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from dugentx.kernel.errors import DuGentXError

Disposer = Callable[[], None]


class EffectScope:
    """一棵 effect 树。`dispose()` 按注册的相反顺序撤销全部。"""

    def __init__(self, name: str = "", parent: EffectScope | None = None) -> None:
        self.name = name
        self.parent = parent
        self._disposers: list[tuple[str, Disposer]] = []
        self._disposed = False

    @property
    def disposed(self) -> bool:
        return self._disposed

    def add(self, disposer: Disposer, *, label: str = "") -> Disposer:
        """挂上一个 disposer，返回一个「只撤销这一笔」的句柄。"""
        if self._disposed:
            raise DuGentXError(f"effect 作用域 {self.name!r} 已经关闭，不能再注册")

        entry = (label or getattr(disposer, "__name__", "effect"), disposer)
        self._disposers.append(entry)

        def single() -> None:
            if entry in self._disposers:
                self._disposers.remove(entry)
                disposer()

        return single

    def child(self, name: str) -> EffectScope:
        """开一个子作用域；父作用域关闭时它会一起关闭。"""
        scope = EffectScope(name=name, parent=self)
        self._disposers.append((f"child:{name}", scope.dispose))
        return scope

    def dispose(self) -> None:
        """撤销全部注册。后进先出——先建立的先拆掉它依赖的东西。"""
        if self._disposed:
            return
        self._disposed = True
        failures: list[BaseException] = []
        while self._disposers:
            label, disposer = self._disposers.pop()
            try:
                disposer()
            except BaseException as exc:  # 卸载失败不能让剩下的拆不掉
                failures.append(exc)
                self._report(label, exc)
        if failures:
            raise DuGentXError(
                f"effect 作用域 {self.name!r} 有 {len(failures)} 笔撤销失败"
            ) from failures[0]

    @staticmethod
    def _report(label: str, exc: BaseException) -> None:
        import sys

        print(f"[dugentx] 撤销 {label!r} 时出错：{exc!r}", file=sys.stderr)

    def __enter__(self) -> EffectScope:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.dispose()
