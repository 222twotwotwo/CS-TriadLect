"""边界检查：把两条硬规则变成可执行的断言。

规则写在 README 里只是意愿，写在脚本里才是约束。这个脚本做三件事：

1. **只有 `providers/llm_anyllm.py` 能 import `any_llm`。**
   别的地方想调模型，必须经过 `ctx.llm`。悄悄绕过缝的代码会让
   「换 provider 不改代码」这句话当场变成假话。

2. **任何地方都不许 import 网络库。** `requests` / `httpx` / `aiohttp` /
   `urllib.request` / `socket` 统统禁止。这一条比第一条更硬：
   LLM 走 any-llm，其余能力不许自己造 HTTP。

3. **`dugentx/kernel/` 不许 import `dugentx/seams/` 或 `dugentx/providers/`。**
   内核是「可拔插」本身，它一旦认识某个具体能力，就不再是内核了。

用法：

    python scripts/check_boundaries.py
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "dugentx"

LLM_ADAPTER = Path("providers/llm_anyllm.py")
FORBIDDEN_NETWORK = {
    "requests",
    "httpx",
    "aiohttp",
    "urllib.request",
    "urllib3",
    "socket",
    "http.client",
    "websockets",
}


def imports_of(path: Path) -> set[str]:
    """收集一个文件里出现的所有 import 目标（含函数内 import）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def main() -> int:
    problems: list[str] = []

    for path in sorted(PACKAGE.rglob("*.py")):
        relative = path.relative_to(PACKAGE)
        names = imports_of(path)

        if relative != LLM_ADAPTER and any(
            name == "any_llm" or name.startswith("any_llm.") for name in names
        ):
            problems.append(
                f"{relative} 引用了 any_llm；只有 {LLM_ADAPTER} 可以。"
                f"要调模型就通过 ctx.llm。"
            )

        for banned in FORBIDDEN_NETWORK:
            if any(name == banned or name.startswith(banned + ".") for name in names):
                problems.append(
                    f"{relative} 引用了网络库 {banned!r}。"
                    f"DugentX 自己不发 HTTP 请求。"
                )

        if relative.parts[0] == "kernel":
            leaked = [
                name
                for name in names
                if name.startswith(
                    ("dugentx.seams", "dugentx.providers", "dugentx.tools", "dugentx.plugins")
                )
            ]
            if leaked:
                problems.append(
                    f"{relative} 从内核反向依赖了 {leaked}；"
                    f"内核不该认识任何具体能力。"
                )

    if problems:
        print("边界检查失败：")
        for problem in problems:
            print("  ✗ " + problem)
        return 1

    print("边界检查通过：LLM 只走 any-llm，没有自造 HTTP，内核保持中立。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
