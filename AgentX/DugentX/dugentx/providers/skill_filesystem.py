"""skill 缝的默认 provider —— 一个目录一棵树，`<name>/SKILL.md` 就是一个技能。

技能是「写给模型看的说明」，不是「模型能执行的动作」。把它做成文件而不是
代码，是因为改一次说明不该重新部署：讲解某个流程、某个工具的坑、某个仓库的
约定，本来就是有人随时会想改的东西。

两件事决定了这个 provider 的形状：

- **目录常驻、正文按需**。`catalog()` 只读 front matter，产出给模型看的一行
  摘要（名字 + 用途 + 什么时候用）；正文在 `load()` 时才从磁盘上读出来。
  挂了五十个技能，平时的上下文成本也只是五十行。
- **front matter 可以缺**。缺了就用目录名当技能名、用第一个标题当描述。
  让一个写得很随手的 `SKILL.md` 因为少了几行 `---` 就装不进去，是拿形式
  去挡内容；技能的价值全在正文里。

token 数用 `compaction.estimate_tokens` 粗估，估的是**正文**——
真正进上下文的是正文，front matter 只进目录。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

from dugentx.kernel.errors import DuGentXError, PluginError, ToolError
from dugentx.kernel.events import EventBus
from dugentx.seams.compaction import estimate_tokens
from dugentx.seams.skill import SkillInfo

SKILL_FILENAME = "SKILL.md"
"""技能文件名。大写是约定：一个目录里可能有 README，而 SKILL.md 是「给模型看的」那一份。"""

_FRONT_MATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n?", re.DOTALL)
_HEADING = re.compile(r"^#{1,6}\s+(.*)$", re.MULTILINE)


@dataclass(slots=True)
class SkillRecord:
    """一个技能：给它看的元信息，和真正会进上下文的正文。"""

    info: SkillInfo
    body: str


class FilesystemSkills:
    """`ctx.skills` 的默认实现。"""

    CONFIG_KEYS = frozenset({"directories"})

    def __init__(
        self, directories: Sequence[str | Path], *, events: EventBus | None = None
    ) -> None:
        if not directories:
            raise PluginError(
                "FilesystemSkills 至少要一个技能目录；一个都没有的话，"
                "这个插件装了等于没装，而模型会以为它有技能可用"
            )
        self.directories = [Path(item) for item in directories]
        self.events = events
        self._cache: dict[Path, tuple[int, int, SkillRecord]] = {}

    @classmethod
    def from_config(
        cls, config: dict[str, object], *, events: EventBus | None = None
    ) -> FilesystemSkills:
        """从配置行里长出 provider。未知键和不存在的目录都在装载时报错。

        「目录配错了」如果留到运行期，表现出来是「技能怎么没生效」——
        这句话的排查成本远高于装载时的一句报错。所以这里逐个 stat 一遍。
        """
        unknown = set(config) - cls.CONFIG_KEYS
        if unknown:
            raise PluginError(
                f"skill 配置里有不认识的键：{sorted(unknown)}；可用：{sorted(cls.CONFIG_KEYS)}"
            )
        raw = config.get("directories")
        if not isinstance(raw, (list, tuple)) or not raw:
            raise PluginError(
                f"skill 插件的 config.directories 必须是一个非空列表，收到 {raw!r}"
            )
        directories = [str(item) for item in raw]
        for directory in directories:
            if not Path(directory).is_dir():
                raise PluginError(f"技能目录不存在：{directory!r}（按进程的工作目录解析）")
        return cls(directories, events=events)

    # ---------------------------------------------------------------- 目录

    async def catalog(self) -> list[SkillInfo]:
        """列出所有技能的一句话摘要。

        每次调用都重新 stat 一遍文件：技能是人手改的，改完就该立刻生效，
        所以缓存的条件是「文件的修改时间和大小都没变」，而不是时间。
        """
        infos = [record.info for record in self._all().values()]
        if self.events is not None:
            self.events.emit("skill/catalog", len(infos))
        return infos

    async def load(self, name: str) -> str:
        """读出技能正文（不含 front matter）。

        名字打错是**可预期**的失败，所以抛 `ToolError`：它会变成一条 tool 消息
        回到模型面前，里面还带着现有技能的名单，模型有机会自己改过来。
        抛别的异常会把这个 step 打断，而它本来只是一次拼写问题。
        """
        records = self._all()
        record = records.get(name)
        if record is None:
            available = "、".join(sorted(records)) or "（一个技能都没有）"
            raise ToolError(f"没有名为 {name!r} 的技能；现有：{available}")
        return record.body

    def path_of(self, name: str) -> str:
        """技能文件在哪。给「这个技能是从哪个文件来的」这类问题用。"""
        record = self._all().get(name)
        return record.info.path if record is not None else ""

    # ---------------------------------------------------------------- 扫描

    def _all(self) -> dict[str, SkillInfo]:
        found: dict[str, SkillRecord] = {}
        for directory in self.directories:
            if not directory.is_dir():
                continue
            for child in sorted(directory.iterdir()):
                path = child / SKILL_FILENAME
                if not child.is_dir() or not path.is_file():
                    continue
                record = self._read(path, child.name)
                # 前面的目录赢：这样可以拿一个本地目录覆盖课程自带的例子，
                # 而不用去改例子本身。
                found.setdefault(record.info.name, record)
        return found

    def _read(self, path: Path, fallback_name: str) -> SkillRecord:
        stat = path.stat()
        cached = self._cache.get(path)
        if cached is not None and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
            return cached[2]

        text = path.read_text(encoding="utf-8")
        record = parse_skill(text, fallback_name=fallback_name, path=str(path))
        self._cache[path] = (stat.st_mtime_ns, stat.st_size, record)
        return record


def parse_skill(text: str, *, fallback_name: str, path: str = "") -> SkillRecord:
    """把一份 `SKILL.md` 拆成（元信息，正文）。

    元信息全都可以缺，缺了就用文件自己的东西顶上：目录名当名字、
    第一个标题当描述。**这不是宽容，而是取舍**——技能的成本在正文，
    形式上的完整不产生任何价值，而一道形式上的门槛会让人干脆不写技能。
    """
    matched = _FRONT_MATTER.match(text)
    meta: dict[str, object] = {}
    body = text
    if matched is not None:
        raw = matched.group(1)
        try:
            loaded = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            raise DuGentXError(
                f"{path or 'SKILL.md'} 的 front matter 不是合法的 YAML：{exc}"
            ) from exc
        if loaded is not None and not isinstance(loaded, dict):
            raise DuGentXError(
                f"{path or 'SKILL.md'} 的 front matter 必须是一个映射"
                f"（name/description/when_to_use），收到 {type(loaded).__name__}"
            )
        meta = dict(loaded or {})
        body = text[matched.end() :]

    body = body.strip()
    heading = _HEADING.search(body)
    description = str(meta.get("description") or (heading.group(1).strip() if heading else ""))
    info = SkillInfo(
        name=str(meta.get("name") or fallback_name),
        description=description or "（这个技能没写描述）",
        path=path,
        when_to_use=str(meta.get("when_to_use") or ""),
        tokens=estimate_tokens(body),
    )
    return SkillRecord(info=info, body=body)
