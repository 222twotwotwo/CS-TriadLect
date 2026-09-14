"""技能的测试：目录能否被发现、正文能否按需取出、失败是否说人话。

这些测试直接读仓库里的 `examples/skills`——那两个 SKILL.md 是演示内容，
如果它们哪天被改坏了，技能这条链路的测试应该跟着红，而不是继续绿着。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dugentx.events import EVENT_MODES
from dugentx.kernel.context import Context
from dugentx.kernel.errors import PluginError, ToolError
from dugentx.kernel.events import EventBus
from dugentx.plugins.skill import create as create_skill
from dugentx.providers.skill_filesystem import FilesystemSkills
from dugentx.seams.messages import ToolCall
from dugentx.seams.session import SessionLog
from dugentx.seams.tools import ToolRegistry
from dugentx.tools.skill_tools import register as register_skill_tool

SKILLS_DIR = Path(__file__).resolve().parents[1] / "examples" / "skills"


def _ctx(**services: object) -> Context:
    ctx = Context("test", events=EventBus(EVENT_MODES))
    for key, value in services.items():
        ctx.provide(key, value)
    return ctx


# ------------------------------------------------------------------ 目录


async def test_catalog_finds_both_example_skills() -> None:
    provider = FilesystemSkills([SKILLS_DIR], events=EventBus(EVENT_MODES))
    infos = await provider.catalog()

    assert [info.name for info in infos] == ["reviewing-a-diff", "writing-a-commit-message"]
    for info in infos:
        assert info.description
        assert info.when_to_use  # 两个例子都写了「什么时候用」
        assert info.tokens > 0
        assert info.path.endswith("SKILL.md")


async def test_catalog_reports_the_count_as_an_event() -> None:
    """「目录被读了一遍」是值得观测的动作：谁在什么时候把技能列表拉走了。"""
    bus = EventBus(EVENT_MODES)
    seen: list[int] = []
    bus.on("skill/catalog", lambda count: seen.append(count))

    provider = FilesystemSkills([SKILLS_DIR], events=bus)
    await provider.catalog()
    assert seen == [2]


# ------------------------------------------------------------------ 正文


async def test_load_returns_the_body_with_the_front_matter_stripped() -> None:
    provider = FilesystemSkills([SKILLS_DIR])
    body = await provider.load("writing-a-commit-message")

    assert not body.startswith("---")
    assert "name: writing-a-commit-message" not in body
    assert "when_to_use:" not in body
    assert body.startswith("# 写一条提交信息")
    assert "git diff --staged" in body


async def test_unknown_name_raises_tool_error_listing_the_available() -> None:
    """名字打错是可预期的失败：抛 ToolError，让模型拿到名单自己改。"""
    provider = FilesystemSkills([SKILLS_DIR])
    with pytest.raises(ToolError) as info:
        await provider.load("write-commit-message")
    assert "writing-a-commit-message" in str(info.value)
    assert "reviewing-a-diff" in str(info.value)


async def test_a_skill_without_front_matter_falls_back_to_the_directory_and_first_heading(
    tmp_path: Path,
) -> None:
    """形式上少几行不该挡住内容：缺 front matter 就用目录名和第一个标题。"""
    folder = tmp_path / "随手记"
    folder.mkdir()
    (folder / "SKILL.md").write_text("# 怎么记笔记\n\n先写结论，再写理由。\n", encoding="utf-8")

    infos = await FilesystemSkills([tmp_path]).catalog()
    assert infos[0].name == "随手记"
    assert infos[0].description == "怎么记笔记"
    assert infos[0].when_to_use == ""


async def test_an_earlier_directory_wins_a_name_collision(tmp_path: Path) -> None:
    """能覆盖才有用：本地目录盖住课程自带的例子，不用去改例子本身。"""
    first = tmp_path / "a"
    second = tmp_path / "b"
    for root, text in ((first, "# 本地版\n"), (second, "# 例子版\n")):
        (root / "同名技能").mkdir(parents=True)
        (root / "同名技能" / "SKILL.md").write_text(text, encoding="utf-8")

    provider = FilesystemSkills([first, second])
    infos = await provider.catalog()
    assert len(infos) == 1
    assert "本地版" in await provider.load("同名技能")


# ------------------------------------------------------------------ 插件与工具


async def test_the_plugin_provides_a_provider_built_from_its_config() -> None:
    ctx = _ctx()
    dispose = await ctx.mount(create_skill({"directories": [str(SKILLS_DIR)]}))
    assert isinstance(ctx.service("skills"), FilesystemSkills)
    assert (await ctx.service("skills").catalog())[0].name == "reviewing-a-diff"

    dispose()
    assert not ctx.has("skills")


async def test_a_missing_skill_directory_fails_loudly_at_load_time() -> None:
    """目录配错了要在装载时吵，不要留到「技能怎么没生效」。"""
    ctx = _ctx()
    with pytest.raises(PluginError) as info:
        await ctx.mount(create_skill({"directories": ["不存在的目录"]}))
    assert "不存在的目录" in str(info.value)

    with pytest.raises(PluginError):
        await ctx.mount(create_skill({"directorie": [str(SKILLS_DIR)]}))


async def test_use_skill_returns_the_body_and_records_it_in_the_log() -> None:
    """加载动作进日志：这一轮它看过哪些说明，是可以回查的事实。"""
    log = SessionLog("s-main")
    ctx = _ctx(session=log, skills=FilesystemSkills([SKILLS_DIR]))
    ctx.provide("tools", ToolRegistry(ctx))
    dispose = register_skill_tool(ctx)

    outcome = await ctx.service("tools").execute(
        ToolCall(id="c1", name="use_skill", arguments={"name": "reviewing-a-diff"})
    )

    assert outcome.ok
    assert "# 审一段 diff" in outcome.content
    event = log.last("skill/loaded")
    assert event is not None
    assert event.data["name"] == "reviewing-a-diff"
    assert event.data["tokens"] > 0

    dispose()
    assert ctx.service("tools").names() == []


async def test_use_skill_reports_an_unknown_name_back_to_the_model() -> None:
    ctx = _ctx(session=SessionLog("s-main"), skills=FilesystemSkills([SKILLS_DIR]))
    ctx.provide("tools", ToolRegistry(ctx))
    register_skill_tool(ctx)
    outcome = await ctx.service("tools").execute(
        ToolCall(id="c1", name="use_skill", arguments={"name": "没有这个"})
    )

    assert not outcome.ok
    assert "reviewing-a-diff" in outcome.content
