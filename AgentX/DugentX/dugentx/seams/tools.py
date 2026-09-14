"""tools 缝 —— 服务定义与执行管道。

模型会「请求」调用工具，但**执行权在 harness 手里**。这条区分如果只是
一句话，它就什么也不是；DugentX 把它落成一个四段管道：

```
tool/call                      （通知：模型请求了一次调用）
  tools/pre-execute            （waterfall：策略层。可以改写、可以拦、可以拒绝）
    tools/execute              （waterfall，terminal 是工具自己的处理器）
  tools/post-execute           （waterfall：结果后处理、脱敏、截断）
tool/result                    （通知：结果已经回填）
```

权限（permissions 缝）、沙箱、审计、结果裁剪全都挂在 `tools/pre-execute`
上面——**没有一个工具需要知道这些政策存在**。这就是「插件，而不是改循环」。

工具 schema 由 Python 函数自己长出来（签名 + 类型标注 + docstring 的 Args 段）。
这是 harness 该做的事：模型看不到你的类型标注，它只看到 JSON schema，
而把两者对齐是机械工作，应该自动完成。
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, Union, get_args, get_origin, get_type_hints

from dugentx.kernel.context import Context
from dugentx.kernel.effect import Disposer
from dugentx.kernel.errors import ToolBlocked, ToolError
from dugentx.seams.messages import ToolCall

LABEL_READ = "read"
LABEL_WRITE = "write"
LABEL_DANGEROUS = "dangerous"
LABEL_NETWORK = "network"
"""工具标签。权限策略按标签分级，而不是按工具名字硬编码——
新加一个写文件的工具，只要打上 write，权限策略自动管住它。"""


@dataclass(slots=True)
class ToolOutcome:
    """一次工具执行的结果。失败也是一种结果，会被回传给模型。"""

    call_id: str
    name: str
    content: str
    ok: bool = True
    blocked: bool = False
    note: str = ""

    def to_text(self) -> str:
        if self.ok:
            return self.content
        head = "已拦截" if self.blocked else "执行失败"
        return f"[{head}] {self.content}" + (f"（{self.note}）" if self.note else "")


@dataclass(slots=True)
class Tool:
    """一个模型可调用的能力。"""

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., Any]
    labels: frozenset[str] = frozenset()
    title: str = ""

    def spec(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """`ctx.tools` —— 工具注册表 + 执行管道。"""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx
        self._tools: dict[str, Tool] = {}

    # ---------------------------------------------------------------- 注册

    def register(self, tool: Tool) -> Disposer:
        if tool.name in self._tools:
            raise ToolError(f"工具 {tool.name!r} 已经注册过了；工具名必须唯一")
        self._tools[tool.name] = tool

        def dispose() -> None:
            if self._tools.get(tool.name) is tool:
                del self._tools[tool.name]

        return dispose

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError:
            raise ToolError(
                f"没有名为 {name!r} 的工具；现有：{sorted(self._tools)}"
            ) from None

    def names(self) -> list[str]:
        return sorted(self._tools)

    def all(self) -> list[Tool]:
        return [self._tools[n] for n in sorted(self._tools)]

    def specs(self) -> list[dict[str, Any]]:
        """给模型的工具 schema 列表。"""
        return [t.spec() for t in self.all()]

    # ---------------------------------------------------------------- 执行

    async def execute(self, call: ToolCall) -> ToolOutcome:
        """跑一次工具调用，全程经过管道。任何异常都变成结果，不往外抛。"""
        self._ctx.events.emit("tool/call", call)

        async def terminal(current: ToolCall) -> ToolOutcome:
            # `self.get()` 必须在 try 里面：名字写错是很常见的模型错误
            # （它会在几十个工具里挑错一个），而这种错误应该是**一条结果**，
            # 让模型看到「没有这个工具，现有的是这些」然后自己改。
            # 把它抛出去会打断整个 step，代价远大于收益。
            try:
                tool = self.get(current.name)
                value = tool.handler(**self._handler_kwargs(tool, current))
                if inspect.isawaitable(value):
                    value = await value
            except ToolError as exc:
                return ToolOutcome(
                    call_id=current.id,
                    name=current.name,
                    content=str(exc),
                    ok=False,
                    blocked=isinstance(exc, ToolBlocked),
                )
            except Exception as exc:  # 工具的 bug 不该打断整个会话
                return ToolOutcome(
                    call_id=current.id,
                    name=current.name,
                    content=f"{type(exc).__name__}: {exc}",
                    ok=False,
                    note="异常已捕获，已作为结果回传给模型",
                )
            return ToolOutcome(
                call_id=current.id, name=current.name, content=str(value)
            )

        try:
            outcome = await self._ctx.events.waterfall(
                "tools/pre-execute", call, terminal=terminal
            )
            if not isinstance(outcome, ToolOutcome):
                raise ToolError(
                    f"tools/pre-execute 返回了 {type(outcome).__name__}，应当是 ToolOutcome"
                )
        except ToolBlocked as exc:
            tool = self._tools.get(call.name)
            outcome = ToolOutcome(
                call_id=call.id,
                name=call.name,
                content=str(exc),
                ok=False,
                blocked=True,
                note=f"标签={sorted(tool.labels)}" if tool else "",
            )

        outcome = await self._ctx.events.waterfall(
            "tools/post-execute", outcome, terminal=_identity_outcome
        )
        self._ctx.events.emit("tool/result", outcome)
        return outcome

    def _handler_kwargs(self, tool: Tool, call: ToolCall) -> dict[str, Any]:
        """把参数交给处理器。

        参数从模型给的 JSON 来，**不保证合法**：可能缺参、可能类型不对。
        这里不替模型纠正，而是让缺失的键变成 `TypeError` 被下面的
        `except Exception` 接住，作为一条 tool 消息回给模型——
        它拿到「缺 path 参数」比拿到一个被填充了默认值的调用更有用。
        """
        kwargs: dict[str, Any] = dict(call.arguments)
        signature = inspect.signature(tool.handler)
        if "ctx" in signature.parameters:
            kwargs["ctx"] = self._ctx
        if "call" in signature.parameters:
            kwargs["call"] = call
        return kwargs


async def _identity_outcome(outcome: ToolOutcome) -> ToolOutcome:
    return outcome


# -------------------------------------------------------------------- schema


def tool_from_function(
    fn: Callable[..., Any],
    *,
    name: str | None = None,
    labels: frozenset[str] = frozenset(),
    description: str | None = None,
) -> Tool:
    """从一个 Python 函数长出工具定义。

    函数需要：完整类型标注、docstring、以及 docstring 里的 `Args:` 段。
    缺了任何一个都会**在这里报错**，而不是等模型调到它才发现描述是空的——
    模型选不选这个工具、参数填得对不对，全靠这段文字。
    """
    signature = inspect.signature(fn)
    hints = get_type_hints(fn)
    doc = inspect.getdoc(fn) or ""

    summary, arg_docs = _split_docstring(doc)
    properties: dict[str, Any] = {}
    required: list[str] = []

    for param_name, param in signature.parameters.items():
        if param_name in {"self", "ctx", "call"}:
            continue
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        annotation = hints.get(param_name, param.annotation)
        if annotation is inspect.Parameter.empty:
            raise ToolError(
                f"工具 {fn.__name__!r} 的参数 {param_name!r} 没有类型标注；"
                f"模型要从标注生成 schema，缺了它就只能靠猜"
            )
        properties[param_name] = _json_schema(annotation, arg_docs.get(param_name, ""))
        if param.default is inspect.Parameter.empty:
            required.append(param_name)

    if not properties and arg_docs:
        pass  # 无参工具合法
    schema: dict[str, Any] = {"type": "object", "properties": properties, "required": required}

    return Tool(
        name=name or fn.__name__,
        description=(description or summary).strip() or f"调用 {fn.__name__}",
        parameters=schema,
        handler=fn,
        labels=labels,
        title=fn.__name__,
    )


def _split_docstring(doc: str) -> tuple[str, dict[str, str]]:
    """把 docstring 拆成「一句话摘要」和「参数名 → 说明」。"""
    lines = doc.splitlines()
    summary_lines: list[str] = []
    arg_docs: dict[str, str] = {}
    in_args = False
    current: str | None = None

    for line in lines:
        stripped = line.strip()
        if stripped in {"Args:", "Arguments:", "参数:", "参数："}:
            in_args = True
            continue
        if stripped in {"Returns:", "Raises:", "Yields:", "返回:"}:
            in_args = False
            current = None
            continue
        if not in_args:
            summary_lines.append(line)
            continue
        if not stripped:
            continue
        if ":" in stripped and not stripped.startswith("-"):
            key, _, rest = stripped.partition(":")
            key = key.strip()
            if " " not in key:
                current = key
                arg_docs[current] = rest.strip()
                continue
        if current is not None:
            arg_docs[current] = (arg_docs[current] + " " + stripped).strip()

    summary = "\n".join(summary_lines).strip()
    return summary, arg_docs


_PRIMITIVES: dict[Any, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}


def _json_schema(annotation: Any, description: str) -> dict[str, Any]:
    """把 Python 标注翻译成 JSON schema 的一小段。

    故意只支持最常见的那几种。遇到不认识的类型就退化成 string，
    并在描述里说明——比抛异常好：一个工具描述得不够精确，
    不该让整个 harness 装不起来。
    """
    base: dict[str, Any] = {}
    origin = get_origin(annotation)

    if annotation in _PRIMITIVES:
        base = {"type": _PRIMITIVES[annotation]}
    elif origin is Union or str(origin) == "<class 'types.UnionType'>":
        args = [a for a in get_args(annotation) if a is not type(None)]
        base = _json_schema(args[0], "") if args else {"type": "string"}
    elif origin in (list, tuple, set):
        args = get_args(annotation)
        item = _json_schema(args[0], "") if args else {"type": "string"}
        base = {"type": "array", "items": item}
    elif origin is dict:
        base = {"type": "object"}
    elif hasattr(annotation, "__members__"):  # 枚举
        base = {"type": "string", "enum": [m.value for m in annotation]}  # type: ignore[attr-defined]
    else:
        base = {"type": "string"}

    if description:
        base["description"] = description
    return base


class ToolRunner(Protocol):
    """给那些只想拿一个「能跑工具的 callable」的地方用。"""

    async def __call__(self, call: ToolCall) -> ToolOutcome: ...
