"""从事件目录生成 `docs/events.md`。

为什么事件文档要生成，而不是手写：**手写的表一定会烂。**
事件名字改一个字母，文档不会报错，只会静静地骗人——而「静静骗人」
正是这个仓库最想避免的事（见 `dugentx/events.py` 的模块注释）。

这个脚本做两件事：

1. 把 `dugentx.events.EVENTS` 渲成表。目录是唯一真相，文档是它的投影。
2. **扫描谁发、谁听**——用 AST 找注册与派发调用。这件事单看任何一个文件
   都看不出来：`paint.py` 在方法开头写 `on = self.ctx.on`，然后连着注册
   十几条，你不去数就不知道一个事件有几个监听者。

扫描的范围与边界（这里说清楚，免得读者以为它是全知的）：

- 发：`bus.emit("名", ...)` / `bus.waterfall("名", ...)` / `bus.parallel(...)`
  ——所有派发方式都算，因为方法名就是模式名。
- 听：`ctx.on("名", ...)`，以及 `on = ctx.on` 之后再 `on("名", ...)` 的别名写法。
- 名字可以是字符串字面量，也可以是模块级常量（`NAME = "tool/result"`）。
- **不认**的是运行期算出来的名字。那种静态上无从得知，所以下面两张
  「谁发 / 谁在听」的列是**下界**，不是全部。

`--check` 只比较不写入，用来拦住「改了代码没重新生成文档」。
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

from dugentx.events import EVENTS
from dugentx.kernel.events import ALLOWED_MODES

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "dugentx"
TARGET = ROOT / "docs" / "events.md"

# 注册与派发用的方法名。派发那一边直接复用内核的模式集合——
# 方法名就是模式名，所以这里不该再抄一份。
_SUBSCRIBE = "on"
_PUBLISH = {"emit"} | set(ALLOWED_MODES)

# 每种派发方式的语义。这一小块是人写的：它没法从代码里推出来。
# 如果哪天多了一种模式而这个字典没跟上，下面的代码会退化成一行
# 「语义未描述」，不会把它从文档里悄悄漏掉。
_MODE_NOTES: dict[str, tuple[str, str]] = {
    "waterfall": (
        "可以拦下来",
        "环绕中间件。改写参数、拒绝、或者什么都不做放过去；"
        "**必须调 `nxt()`**，否则后面的处理器（包括工具本身）不会执行。"
        "权限拦截、上下文注入、请求改写全挂在这上面。",
    ),
    "emit": ("只能看", "通知。没有返回值，别指望它影响流程。适合日志、指标、界面。"),
    "parallel": ("并发收尾", "互不相干的多件收尾工作，并发跑，顺序不重要。"),
    "serial": ("依次取结果", "按注册顺序依次 await，把每个监听器的返回值收集起来。"),
    "bail": ("谁先答谁赢", "按注册顺序问，第一个给出非 None 的胜出，后面的不再跑。"),
}
_UNKNOWN_MODE = ("其他", "内核支持，但本脚本还没描述它的语义。")


def _module_literals(tree: ast.Module) -> dict[str, str]:
    """模块级 `NAME = "字面量"`，用来解析 `ctx.on(EVENT_X, ...)` 这种写法。"""
    found: dict[str, str] = {}
    for node in tree.body:
        targets: list[ast.expr]
        value: ast.expr | None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if not (isinstance(value, ast.Constant) and isinstance(value.value, str)):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                found[target.id] = value.value
    return found


def _on_aliases(tree: ast.Module) -> set[str]:
    """`on = self.ctx.on` 这类别名。

    `paint.py` 就是这么写的：在方法开头取一次，后面连着注册十几条。
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not (isinstance(node.value, ast.Attribute) and node.value.attr == _SUBSCRIBE):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
    return names


def _resolve(node: ast.expr, literals: dict[str, str]) -> str | None:
    """把参数节点解析成事件名。只认字面量和模块级常量。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return literals.get(node.id)
    return None


def _scan(root: Path) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """扫出每个事件名被谁订阅、被谁发出。"""
    listeners: dict[str, set[str]] = {}
    emitters: dict[str, set[str]] = {}

    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        rel = path.relative_to(ROOT).as_posix()
        literals = _module_literals(tree)
        aliases = _on_aliases(tree)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            name = _resolve(node.args[0], literals)
            if name is None:
                continue
            func = node.func
            subscribes = False
            publishes = False
            if isinstance(func, ast.Attribute):
                subscribes = func.attr == _SUBSCRIBE
                publishes = func.attr in _PUBLISH
            elif isinstance(func, ast.Name):
                subscribes = func.id in aliases
            if subscribes:
                listeners.setdefault(name, set()).add(rel)
            elif publishes:
                emitters.setdefault(name, set()).add(rel)

    return listeners, emitters


def _fmt(files: set[str]) -> str:
    """把文件集合写成一行。多个就折行，别把表挤爆。"""
    if not files:
        return "—"
    ordered = sorted(files)
    if len(ordered) == 1:
        return f"`{ordered[0]}`"
    return "<br>".join(f"`{name}`" for name in ordered)


def render() -> str:
    """渲染整份文档：目录在前，扫描结果在后。"""
    listeners, emitters = _scan(PACKAGE)

    out: list[str] = []
    out.append("<!-- 由 scripts/gen_docs.py 生成，不要手改。 -->")
    out.append("<!-- 改事件请改 dugentx/events.py，然后 `uv run python scripts/gen_docs.py`。 -->")
    out.append("")
    out.append("# 事件目录")
    out.append("")

    used: list[str] = []
    for spec in EVENTS:
        if spec.mode not in used:
            used.append(spec.mode)
    unused = sorted(set(ALLOWED_MODES) - set(used))

    out.append(
        f"内核支持 {len(ALLOWED_MODES)} 种派发方式，当前目录用到 {len(used)} 种。"
        "区别在于**你能不能拦下它**："
    )
    out.append("")
    out.append("| 方式 | 你能做什么 | 说明 |")
    out.append("|---|---|---|")
    for mode in used:
        role, note = _MODE_NOTES.get(mode, _UNKNOWN_MODE)
        out.append(f"| `{mode}` | {role} | {note} |")
    out.append("")
    if unused:
        out.append(
            "当前目录没有用到的："
            + "、".join(f"`{mode}`" for mode in unused)
            + "。内核实现了它们，只是还没有事件声明用上。"
        )
        out.append("")
    out.append(
        "`EventBus` 会**在派发时校验**：发一个声明为 waterfall 的事件用 `emit`，"
        "当场报错；发一个目录里没有的名字，也当场报错。"
        "写错事件名本来是静默失败——监听器永远不触发，没有异常，只有「怎么没生效」。"
    )
    out.append("")

    # waterfall 排最前：写扩展主要用它。
    for mode in sorted(used, key=lambda name: (name != "waterfall", name)):
        specs = [spec for spec in EVENTS if spec.mode == mode]
        role, note = _MODE_NOTES.get(mode, _UNKNOWN_MODE)
        out.append(f"## `{mode}`：{role}")
        out.append("")
        out.append(note)
        out.append("")
        out.append("| 事件 | 载荷 | 含义 | 谁发 | 谁在听 |")
        out.append("|---|---|---|---|---|")
        for spec in specs:
            payload = ", ".join(f"`{name}`" for name in spec.payload) or "—"
            out.append(
                f"| `{spec.name}` | {payload} | {spec.summary} | "
                f"{_fmt(emitters.get(spec.name, set()))} | "
                f"{_fmt(listeners.get(spec.name, set()))} |"
            )
        out.append("")

    unheard = [spec.name for spec in EVENTS if not listeners.get(spec.name)]
    unfired = [spec.name for spec in EVENTS if not emitters.get(spec.name)]

    out.append("## 没人听的事件")
    out.append("")
    if unheard:
        out.append(
            "下面这些有声明、有人发，但**当前代码里没有任何监听者**。"
            "这不一定是错的——它们是对外开放的扩展点，只是还没有插件用。"
            "但如果加完之后这里一直是一长串，值得问一句是不是该删。"
        )
        out.append("")
        for name in unheard:
            out.append(f"- `{name}`")
    else:
        out.append("没有。每个事件至少有一个监听者。")
    out.append("")

    out.append("## 没人发的事件")
    out.append("")
    if unfired:
        out.append(
            "下面这些**声明在目录里，但代码里没有一处派发它们**。"
            "这一类最值得警惕：订阅它们不会报错，只会永远不触发——"
            "名字写错造成的那种静默失败，只不过这次写错的是目录本身。"
            "处理办法只有两个：把它实现，或者把它从目录里删掉。"
            "（扫描只认字面量，所以运行期算出来的事件名会误报，见下。）"
        )
        out.append("")
        for name in unfired:
            out.append(f"- `{name}`")
    else:
        out.append("没有。目录里每个事件都至少有一处派发。")
    out.append("")

    out.append("## 事件名有两个命名空间")
    out.append("")
    out.append(
        "`session/start`、`session/end` 这类名字**只存在于会话日志**里"
        "（`dugentx/seams/session.py` 的 `EventKind`），不在上面这份总线目录里。"
        "两者同名不等于同一个东西："
    )
    out.append("")
    out.append(
        "- **日志种类**是「发生过什么」的记录，只能读，用来投影出模型看到的历史。"
    )
    out.append(
        "- **总线事件**是「此刻可以拦下或反应什么」，只能订阅，用来扩展行为。"
    )
    out.append("")
    out.append(
        "`turn/start` 这类两边都有：边界既值得记进日志，也可能有人要当场反应。"
        "而 `session/start` 只在日志里——一个会话开始了是事实，没有谁需要拦下它。"
    )
    out.append("")

    out.append("## 这张表的边界")
    out.append("")
    out.append(
        "「谁发 / 谁在听」两列是**静态扫描**得到的：认字符串字面量、"
        "模块级常量、以及 `on = ctx.on` 这种别名写法。"
        "运行期算出来的事件名认不出来，而运行期挂载的插件也不在扫描范围里"
        "（比如 `self-extension` 能在跑起来之后再加监听器）。"
        "所以这两列是**下界**，不是全部。"
    )
    out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 docs/events.md")
    parser.add_argument(
        "--check",
        action="store_true",
        help="只比较，不写入；文档过期就退出码 1（给 CI 用）",
    )
    args = parser.parse_args(argv)

    fresh = render()
    try:
        current = TARGET.read_text(encoding="utf-8")
    except FileNotFoundError:
        current = None

    if args.check:
        if current == fresh:
            print("事件文档是最新的。")
            return 0
        print(
            "docs/events.md 与 dugentx/events.py 不一致。\n"
            "跑 `uv run python scripts/gen_docs.py` 重新生成。",
            file=sys.stderr,
        )
        return 1

    if current == fresh:
        print("事件文档没有变化。")
        return 0

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(fresh, encoding="utf-8")
    print(f"已写入 {TARGET.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
