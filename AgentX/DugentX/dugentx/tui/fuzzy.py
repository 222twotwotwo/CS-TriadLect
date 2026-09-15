"""fuzzy —— 名字的模糊匹配。

这里只有一件事：给定一个已经打了一半的字符串，和一堆候选名字，**哪些能匹配、
最像的排前面**。补全弹出（输入框下面那个）和 provider 选择器都读它，所以两边
的「像不像」只有一种答案——两边各写一份的话，同一个 `ds` 在一个地方找得到
`deepseek`、在另一个地方找不到，人只会得出「这个界面时灵时不灵」的结论。

**这个文件故意不认识 textual。** 它是纯函数，于是「打 `ds` 能不能找到
`deepseek-flash`」这件事不需要起一个终端、也不需要装界面库就能被检查。

不做的事：不改大小写以外的东西，不做编辑距离，不认拼音。名字是几十个，
不是几万行代码——十来行子序列匹配够用了，而一个需要调参的匹配器会开始
「猜你想找什么」，那是另一种不诚实。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from typing import TypeVar

T = TypeVar("T")


def fuzzy_score(needle: str, haystack: str) -> int | None:
    """子序列模糊匹配的打分。不匹配返回 None。

    比 `in` 强的地方：打 `dsf` 也能找到 `deepseek-flash`。比正经的模糊匹配弱的
    地方：它只有十来行。这里挑的是几十个名字，不是几万行代码，够用了。

    打分只为一件事服务——**排名**：连续命中的加分，越靠前出现的减分越少。
    """
    if not needle:
        return 0
    score = 0
    previous = -2
    at = 0
    for char in needle:
        found = haystack.find(char, at)
        if found == -1:
            return None
        score += 2 if found == previous + 1 else 0
        score -= min(found, 8) // 2
        previous = found
        at = found + 1
    return score


def filter_names(names: Iterable[str], needle: str) -> list[str]:
    """按模糊匹配筛一遍名字，最像的排前面。空 needle 原样返回。

    过滤是**边打边做**的（每敲一个字重建一次列表），所以这里必须是纯函数、
    且不能有别的副作用：同一次输入，任何时刻的结果都该一样。

    同分时按名字排序：这是给「列一张表让人挑」用的（provider 选择器），
    字母序看着最稳。反过来，补全弹出要的是「第一条就是我会替你按下去的那条」，
    同分时得保住候选自己的顺序——那种场合用 `rank`。
    """
    text = needle.strip().lower()
    if not text:
        return list(names)
    scored: list[tuple[int, str]] = []
    for name in names:
        score = fuzzy_score(text, name.lower())
        if score is not None:
            scored.append((score, name))
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    return [name for _, name in scored]


def rank(items: Sequence[T], needle: str, key: Callable[[T], str]) -> list[T]:
    """按模糊匹配排序，**同分时保持传进来的顺序**。

    为什么同分要保序：弹出里高亮的是第一条，而 Tab 接受的就是高亮那一条。
    所以「第一条是谁」是一个要能解释的判断——候选的顺序是调用方排的
    （命令表是「先看、再改、最后走」，参数补全是「当前那个先给」），
    字母序会把那个判断抹掉。
    """
    text = needle.strip().lower()
    if not text:
        return list(items)
    scored: list[tuple[int, int, T]] = []
    for index, item in enumerate(items):
        score = fuzzy_score(text, key(item).lower())
        if score is not None:
            scored.append((score, index, item))
    scored.sort(key=lambda row: (-row[0], row[1]))
    return [item for _, _, item in scored]
