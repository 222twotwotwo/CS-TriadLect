"""word_count 插件 —— 给 harness 加一只「数词」的手。

一个完整的插件可以只有三样东西，这个文件就是那三样：

1. 一次 `ctx.provide`：把这份配置挂成 `ctx.wordCount`，工具执行时来读它；
2. 一次 `ctx.effect`：注册 `count_words` 工具；
3. 一次 `ctx.effect`：注册一段提示词，让模型知道这个工具存在。

注册全部走 `ctx.effect`。这不是形式：插件被拔掉时（`manage_plugin` 的
unmount、或者 `AgentRuntime.stop()`），作用域会把这两笔一起撤销——
工具和提示词段落同时消失。漏掉任何一笔，留下的就是一个模型看得见、
背后却已经没人的幽灵工具。

它**不碰策略**。工具只打标签（`read` / `write` / `network` / `dangerous`），
要不要放行由 permissions 缝按标签分级决定。这个插件里没有一行 if 在判断
「这次调用该不该允许」——那是配置和代码侧的事，不是工具的事。

配置写在配置行的 `config:` 下：

- `min_length`：长度小于它的词不计入，默认 1。英文按词数，中日韩按字算。
- `top`：最多列出几个高频词，默认 5。
- `labels`：给工具打的标签，默认 `[read]`。
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from dugentx.kernel.context import Context
from dugentx.kernel.effect import Disposer
from dugentx.kernel.errors import PluginError
from dugentx.kernel.plugin import define_plugin
from dugentx.seams.prompt import static
from dugentx.seams.tools import (
    LABEL_DANGEROUS,
    LABEL_NETWORK,
    LABEL_READ,
    LABEL_WRITE,
    tool_from_function,
)

LATIN = re.compile(r"[A-Za-z0-9_']+")
CJK = re.compile(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]")
"""英文按词切，中日韩按字切。

**不做分词**：一个中文的「词」该切几刀，任何规则都会有人不同意，
而这里只是数数。取舍写出来，好过假装它是一份分词器。
"""

KNOWN_LABELS = frozenset({LABEL_READ, LABEL_WRITE, LABEL_NETWORK, LABEL_DANGEROUS})
CONFIG_KEYS = frozenset({"min_length", "top", "labels"})


@dataclass(slots=True)
class WordCountSettings:
    """这份插件的配置。

    它被挂成服务，而不是被工具直接读配置：插件不该知道配置是 YAML 来的还是
    测试里写的一个字面量，而工具该从**服务**拿它需要的东西——和 `read_file`
    从 `ctx.fs` 拿文件系统是同一件事。
    """

    min_length: int = 1
    top: int = 5


def _positive_int(config: dict[str, Any], key: str, default: int) -> int:
    """读一个正整数配置。类型不对、数值不合法都在装载时报错。"""
    try:
        value = int(config.get(key, default))
    except (TypeError, ValueError) as exc:
        raise PluginError(f"word-count 的 {key} 要是一个整数：{exc}") from exc
    if value < 1:
        raise PluginError(f"word-count 的 {key} 至少要是 1，收到 {value}")
    return value


def _labels(raw: Any) -> frozenset[str]:
    """解析工具标签；拼错的标签在这里就报错。

    为什么不能放过：`PermissionPolicy.level_for()` 对不认识的标签会回落到
    `policy.default`（默认 `confirm`）。一个拼错的 `raed` 不会报任何错，
    它只是**悄悄换了一档**——这种错只有在装载时抓得住。
    """
    if raw is None:
        return frozenset({LABEL_READ})
    if not isinstance(raw, (list, tuple)):
        raise PluginError(f"word-count 的 labels 要是一个列表，收到 {raw!r}")
    labels = frozenset(str(item).strip() for item in raw)
    unknown = labels - KNOWN_LABELS
    if unknown:
        raise PluginError(
            f"word-count 的 labels 里有不认识的标签 {sorted(unknown)}；"
            f"可用：{sorted(KNOWN_LABELS)}"
        )
    return labels


def count_words(ctx: Context, text: str) -> str:
    """数一段文本里出现了哪些词、各出现了多少次。

    英文按词数，中日韩按字算。给的是文本本身，不是文件路径——
    要数文件里的内容，先用 read_file 把它读出来再传进来。

    Args:
        text: 要数的文本内容
    """
    settings: WordCountSettings = ctx.service("wordCount")
    tokens = [word.lower() for word in LATIN.findall(text)] + CJK.findall(text)
    kept = [token for token in tokens if len(token) >= settings.min_length]
    dropped = len(tokens) - len(kept)
    if not kept:
        return f"没有可数的内容（{len(text)} 个字符，长度小于 {settings.min_length} 的都不算）"

    counts = Counter(kept)
    ranking = "、".join(f"{token}×{times}" for token, times in counts.most_common(settings.top))
    omitted = f"，另有 {dropped} 个因为太短没算" if dropped else ""
    return (
        f"共 {len(kept)} 个词/字，去重后 {len(counts)} 个{omitted}\n"
        f"出现最多：{ranking}"
    )


def _register_tool(ctx: Context, labels: frozenset[str]) -> Disposer:
    """注册工具，返回撤销它的 disposer。

    工具的 schema 不需要手写：`tool_from_function` 从签名、类型标注和
    docstring 里的 Args 段把它长出来。
    """
    return ctx.service("tools").register(tool_from_function(count_words, labels=labels))


def _register_section(ctx: Context, min_length: int, top: int) -> Disposer:
    """注册提示词段落，返回撤销它的 disposer。

    工具清单那一段是 prompt 插件**每次装配时现取**注册表拼出来的，
    所以新工具自己就会出现在模型眼前。这一段补的是清单装不下的东西：
    阈值是多少、别自己数。
    """
    return ctx.service("prompt").section(
        "word-count",
        static(
            f"要数一段文本里的词，用 count_words 工具，不要自己数："
            f"长度小于 {min_length} 的词不计入，最多列出 {top} 个高频词。"
        ),
        order=45,
        title="数词",
    )


@define_plugin(
    "word-count",
    inject=("tools", "prompt"),
    provides=("wordCount",),
    description="数词工具：一个工具 + 一段提示词 + 一项配置",
)
def create(ctx: Context, config: dict[str, Any]) -> None:
    """装载数词插件。

    `inject=("tools", "prompt")` 是装载顺序的**唯一**来源：装载器据此把这一行
    排在 tools 和 prompt 之后。少了它，这个插件能不能装上就取决于配置里的
    行序碰巧对不对——那种「碰巧正确」会在某次重新排序时变成一次装载失败。
    """
    unknown = set(config) - CONFIG_KEYS
    if unknown:
        raise PluginError(
            f"word-count 配置里有不认识的键：{sorted(unknown)}；可用：{sorted(CONFIG_KEYS)}"
        )
    settings = WordCountSettings(
        min_length=_positive_int(config, "min_length", 1),
        top=_positive_int(config, "top", 5),
    )
    labels = _labels(config.get("labels"))

    ctx.provide("wordCount", settings)
    ctx.effect(lambda inner: _register_tool(inner, labels), label="tool:count_words")
    ctx.effect(
        lambda inner: _register_section(inner, settings.min_length, settings.top),
        label="prompt:word-count",
    )
