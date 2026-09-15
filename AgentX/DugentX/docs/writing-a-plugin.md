# 写一个插件

`docs/architecture.md` 讲的是**为什么**要「注册即 effect」。这一篇是把那件事做一遍：
从零写一个真能跑的插件，加进配置，然后确认自己没有把装载顺序弄坏。

产物是仓库里这三个，跑得起来、也用不着 API key：

- `examples/plugins/word_count.py` —— 插件**模块**（183 行，含注释）
- `examples/plugins/demo.yml` —— 用它的配置
- `examples/dugentx-plugin-clock/` —— 插件**包**：同一个形状做成一个可安装的发行包（第 6 节）

第 1 到 5 节讲模块形态，第 6 节讲包形态。两种都支持，用哪个取决于你要不要把插件交给别人。

```bash
uv run dugentx -c examples/plugins/demo.yml plugins
uv run dugentx -c examples/plugins/demo.yml run "数一下这句话里有哪些词"
```

它给 harness 加两样东西：一个模型可以调用的工具 `count_words`，
和一段告诉模型「这个工具怎么用」的系统提示词。就这两样。

---

## 1. 插件的形状

一个插件是三个东西：**一个模块**、**一个被 `@define_plugin` 装饰的工厂函数**、
以及工厂调用时返回的**一个 `Plugin` 对象**。做成插件包时第一样变成「包里的一个模块」，
另外两样不变（第 6 节）。

```python
@define_plugin(
    "word-count",                     # 插件名（出现在 dugentx plugins 里）
    inject=("tools", "prompt"),       # 装载前必须已经存在的服务键
    provides=("wordCount",),          # 我提供哪个键
    description="数词工具：…",         # 一行说明
)
def create(ctx: Context, config: dict[str, Any]) -> None:
    ...                               # 装载时执行
```

`inject`、`provides`、`description` 都是 `Plugin` 的字段，`apply` 是那个函数本体，
`config` 是配置行 `config:` 那一坨。装载器 (dugentx/kernel/loader.py) 对每一行做四件事：

```python
factory = resolve_factory(row.plugin)      # 已安装的包名，或 `包.模块` / `包.模块:属性`（默认取 create）
plugin = factory(row.config)               # 每个实例拿到**自己的** config 副本
if not isinstance(plugin, Plugin):
    raise PluginError(f"{row.plugin!r} 的工厂返回了 {type(plugin).__name__}，不是 Plugin；…")
```

然后是 `Context.mount`（dugentx/kernel/context.py）：

```python
missing = [k for k in plugin.inject if k not in self._services]
if missing:
    raise PluginError(f"插件 {plugin.name!r} 需要 {missing}，但装载时还没有；当前可用：…")

child = self.child(f"plugin:{plugin.name}")     # 服务共享、注册独立
result = plugin.apply(child, plugin.config)      # ← 你的 create() 在这里跑
if inspect.isawaitable(result):
    await result                                # 同步、异步都行
return child.scope.dispose                       # ← 卸载就是这一句
```

四个可以直接读出来的结论：

- **`apply` 可以是 `async def`。** `prompt` 插件就是异步的，因为它在装载时要 await 一次技能目录。
- **你拿到的是一个子上下文。** 服务是共享的（`ctx.tools` 只有一份），但你挂上去的东西
  属于这个子上下文；卸载 = 把那个作用域整个 `dispose()`。所以插件不需要写 `uninstall`。
- **装载失败不留半个插件。** `apply` 抛异常时 `mount` 先 `child.scope.dispose()` 再往上抛。
- **配置是传进来的，不是你自己去读的。** 插件不需要知道这份配置来自 YAML、命令行还是测试里的
  一个字面量。

---

## 2. 三条规矩

| 规矩 | 为什么 | 违反了会怎样 | 谁会发现 |
|---|---|---|---|
| 注册只走 `ctx.provide` / `ctx.on` / `ctx.effect` | 只有这三条路会返回 `Disposer`，只有它们能撤销 | 卸载后留下幽灵：服务、监听器、工具还在，背后却没人了 | `tests/test_kernel.py::test_a_plugin_that_bypasses_ctx_leaves_ghosts` |
| 只 import 缝，不 import 实现 | 换实现（fs / llm / human）是改配置，不是改调用方 | 换 provider 时要回来改你的插件 | `scripts/check_boundaries.py` |
| 声明你需要什么（`inject`） | 装载顺序是**算出来**的，不是写出来的 | 顺序碰巧对——直到某次重排 | 装载器的拓扑排序 + `mount` 的前置检查 |

### 规矩一：每一笔注册都交给作用域

```python
ctx.provide("wordCount", settings)                                   # 服务
ctx.on("tool/result", counter)                                       # 监听器
ctx.effect(lambda inner: registry.register(tool), label="tool:x")     # 别的注册
```

三个入口都返回 `Disposer`。`effect(fn)` 的语义是：跑 `fn(ctx)`，它的返回值如果是 callable
就用它当撤销动作，不是就记一个空操作（`EffectScope.add(inner or (lambda: None))`）。

**为什么这条是硬规矩**：`mount` 的对称动作就是 `dispose`。没有这条，一个插件被拔掉之后
它留下的东西继续活着——模型还看得见那个工具，`ctx` 里还查得到那个服务。反面写法在测试里
摆着（`test_a_plugin_that_bypasses_ctx_leaves_ghosts`）：

```python
@define_plugin("sloppy", inject=("tools",))
def create(c: Context, config: dict) -> None:
    c.service("tools").register("left_behind")   # 返回值没有交给作用域

dispose = await ctx.mount(create({}))
dispose()
assert ctx.service("tools").names() == ["left_behind"]   # ← 幽灵还在
```

`word_count` 的两笔注册都走 `ctx.effect`。这件事我拆开验过一遍（临时脚本，跑完删了）：

```text
挂上： True True True      # count_words 在工具表里、word-count 在提示词段落里、wordCount 在服务里
拔掉： False False False    # 三个都不在了
```

### 规矩二：只认缝

插件里只出现两类 import：`dugentx.kernel.*`（插件机制本身）和 `dugentx.seams.*`（词汇与接口）。
**不 import `dugentx.providers.*`，也不 import `dugentx.plugins.*`。**

服务从 `ctx.<key>` 拿。`ctx.tools` 这个写法来自 `Context.__getattr__`，它只对已注册的服务键生效，
其他属性照常抛 `AttributeError`——所以 `hasattr(ctx, "tools")` 是可信的，拼错的键不会静默变成 `None`。

这条规矩由 `scripts/check_boundaries.py` 静态断言，它检查三件事：只有
`providers/llm_anyllm.py` import 了 `any_llm`；任何地方都不许 import `requests` / `httpx` /
`aiohttp` / `urllib.request` / `socket`；`dugentx/kernel/` 不许反向 import
`seams` / `providers` / `tools` / `plugins`。

### 规矩三：把依赖写在 `inject` 里

`inject` 是装载顺序的**唯一**来源。装载器把「谁 `provides` 什么」和「谁 `inject` 什么」
拼成一张图，拓扑排序；手写启动顺序在 DugentX 里根本没有地方写。

`word_count` 写的是 `inject=("tools", "prompt")`，因为它要 `ctx.service("tools").register(...)`
和 `ctx.service("prompt").section(...)`。少了任何一个名字，它能不能装上就取决于配置里的行序
碰巧对不对——第 7 节第二个坑讲这个。

`provides` 是给配置校验用的（「这个键到底有没有人提供」），顺便出现在 `dugentx plugins` 里。
它**不**等于「你打算 provide 的键」，而是「你已经 provide 的键」：写错了会让装载器以为有别的
提供者，或者以为没人提供。

---

## 3. 一步步写出 `word_count`

### 3.1 定配置面：三个键，全都校验

```python
KNOWN_LABELS = frozenset({LABEL_READ, LABEL_WRITE, LABEL_NETWORK, LABEL_DANGEROUS})
CONFIG_KEYS = frozenset({"min_length", "top", "labels"})
```

配置键是一份**契约**，所以这里先把它写成常量，然后逐个校验：

- `min_length`：长度小于它的词不计入（默认 1）。英文按词数、中日韩按字算（3.3 节）。
- `top`：最多列几个高频词（默认 5）。
- `labels`：给工具打的权限标签（默认 `['read']`）。

```python
def _positive_int(config: dict[str, Any], key: str, default: int) -> int:
    try:
        value = int(config.get(key, default))
    except (TypeError, ValueError) as exc:
        raise PluginError(f"word-count 的 {key} 要是一个整数：{exc}") from exc
    if value < 1:
        raise PluginError(f"word-count 的 {key} 至少要是 1，收到 {value}")
    return value
```

`labels` 的校验多一句，因为它连着权限（第 4 节）：

```python
labels = frozenset(str(item).strip() for item in raw)
unknown = labels - KNOWN_LABELS
if unknown:
    raise PluginError(
        f"word-count 的 labels 里有不认识的标签 {sorted(unknown)}；"
        f"可用：{sorted(KNOWN_LABELS)}"
    )
```

为什么非要在装载时拦一个拼错的标签：`PermissionPolicy.level_for()` 对不认识的标签会回落到
`policy.default`（默认 `confirm`）。一个写错的 `raed` 不报任何错，它只是**悄悄换了一档**。

### 3.2 配置怎么走到工具手里：挂成服务

工具执行时看不见「配置行」这个东西，它只能从 `ctx` 拿。所以插件把自己的配置挂成一个服务：

```python
@dataclass(slots=True)
class WordCountSettings:
    min_length: int = 1
    top: int = 5
```

```python
ctx.provide("wordCount", settings)
```

链条和 `fs` 那一行是同一条：配置 → provider（`ctx.fs` / 这里的小数据对象）→ 消费它的工具。
这样工具就不需要知道配置从哪来，而 `config` 里的行为开关也不会跑到模型的 schema 里去。

**顺带说清一件事**：这个服务不是第二件「扩展点」，它只是配置的载体。
这个插件对外的扩展点是**两样**：一个工具、一段提示词。

### 3.3 工具函数：schema 从签名长出来

```python
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
```

三件事值得点出来：

- **`ctx` 是第一个参数，但它不进 schema。** 注册表按参数名把当前上下文注进去
  （`ToolRegistry._handler_kwargs`），模型看不到它。
- **docstring 是给模型的，不是给你的。** `Args:` 之前的部分成为工具的 `description`，
  `Args:` 段成为每个参数的说明。模型选不选它、参数填得对不对，全靠这段文字。
- **返回值是给模型读的一段话，不是给程序用的数据结构。** 所以「一共多少、忽略了几个」都写出来。
  少写一个字，模型就会以为自己看到了全部。

两个正则（`LATIN` / `CJK`）与「不做分词」的取舍都写在文件里：中文的一个「词」该切几刀，
任何规则都会有人不同意，而这里只是数数。取舍写出来，好过假装它是一份分词器。

### 3.4 两笔注册，两个 Disposer

```python
def _register_tool(ctx: Context, labels: frozenset[str]) -> Disposer:
    return ctx.service("tools").register(tool_from_function(count_words, labels=labels))


def _register_section(ctx: Context, min_length: int, top: int) -> Disposer:
    return ctx.service("prompt").section(
        "word-count",
        static(
            f"要数一段文本里的词，用 count_words 工具，不要自己数："
            f"长度小于 {min_length} 的词不计入，最多列出 {top} 个高频词。"
        ),
        order=45,
        title="数词",
    )
```

- `ToolRegistry.register(tool)` 返回 `Disposer`（工具名重复会抛 `ToolError`）。
- `PromptRegistry.section(name, render, *, order=100, title="")` 返回 `Disposer`
  （名字重复会抛 `ValueError`）。`order` 决定它排在提示词的哪儿：仓库自带的五段是
  10 / 20 / 30 / 40 / 90，这里是 45——工具清单（40）之后、部署方规则（90）之前。
  `static(text)` 是缝里给的小工具，把一段固定文字变成一个 `Render`。

**为什么提示词也要注册一段？** 工具清单那一段是 prompt 插件**每次装配时现取注册表**拼出来的，
所以 `count_words` 自己就会出现在模型眼前。这一段补的是清单装不下的东西：阈值是多少、别自己数。

### 3.5 工厂：校验 → provide → 两笔 effect

```python
@define_plugin(
    "word-count",
    inject=("tools", "prompt"),
    provides=("wordCount",),
    description="数词工具：一个工具 + 一段提示词 + 一项配置",
)
def create(ctx: Context, config: dict[str, Any]) -> None:
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
```

顺序也是有意的：**先把配置校验完，再做注册**。任何一处失败都发生在 `provide` 之前，
不会留下一个挂了一半的插件。`label=` 不是必须的，但它出现在卸载失败时的报错里
（`[dugentx] 撤销 'tool:count_words' 时出错：…`），值得写。

整份文件在 `examples/plugins/word_count.py`，183 行，其中干活的就是一个工具函数、
两个注册函数、一个工厂。

### 3.6 跑起来看

配置（`examples/plugins/demo.yml`，全文 36 行）：

```yaml
extends: ../../dugentx.replay.yml

plugins:
  - id: word-count
    plugin: examples.plugins.word_count
    config:
      min_length: 3
      top: 3
      labels: [read]

patch:
  - id: llm
    config:
      adapter: replay
      replay:
        on_exhausted: error
        turns:
          - - tool_call: count_words
              arguments:
                text: "the quick brown fox jumps over the lazy dog, and the dog barks"
          - - text: "数完了。这句话里 the 出现最多，一共 3 次——这就是 count_words 工具干的事，它是 examples/plugins/word_count.py 挂上来的。"
```

三处配置上的事，都是 `docs/configuration.md` 里的规则：

- `extends` 的相对路径按**这份文件所在的目录**解析，所以两层 `../..` 才到仓库根；
  基座的行先来，自己这一行追加在后面。
- 基座的 `llm` 行用的脚本是「读文件 / 挂插件 / 用技能」，这里换成一段只调用 `count_words`
  的脚本。`patch` 是**整行 config 替换**，不是深合并，所以 `adapter` 和 `replay` 都要重写。
- 插件模块路径要能被 import。`examples/plugins/word_count.py` 能解析成
  `examples.plugins.word_count`，是因为可编辑安装把仓库根放进了 `sys.path`
  （`.venv/Lib/site-packages/_editable_impl_dugentx.pth`），而 `examples/` 与
  `examples/plugins/` 没有 `__init__.py` 也能当命名空间包用。你的插件放在一个真包里更稳。

第一件事：它装上了没有。

```console
$ uv run dugentx -c examples/plugins/demo.yml plugins
配置：examples/plugins/demo.yml
工作目录：C:\Users\Frees\Documents\Obsdian\Working\Agent学习\DugentX

按装载顺序：
   1. tools            装 tools            需要[—]
      dugentx.plugins.tools                      提供[tools]
   2. session          装 session          需要[—]
      dugentx.plugins.session                    提供[session, sessionStore]
   3. prompt           装 prompt           需要[tools]
      dugentx.plugins.prompt                     提供[prompt]
   4. llm              装 llm              需要[—]
      dugentx.plugins.llm                        提供[llm, models]
   5. agent-loop       装 agentLoop        需要[llm, tools, session]
      dugentx.providers.agent_loop_basic         提供[agentLoop]
   6. fs               装 fs               需要[—]
      dugentx.plugins.fs                         提供[fs]
   7. shell            装 shell            需要[—]
      dugentx.plugins.shell                      提供[shell]
   8. permissions      装 permissions      需要[tools]
      dugentx.plugins.permissions                提供[permissions, approval]
   9. compaction       装 compaction       需要[session]
      dugentx.plugins.compaction                 提供[compaction]
  10. agent            装 agent            需要[session, tools]
      dugentx.plugins.agent                      提供[agents, agent]
  11. subagent         装 subagent         需要[agents]
      dugentx.plugins.subagent                   提供[subagents]
  12. fs-tools         装 fs_tools         需要[tools, fs]
      dugentx.tools.fs_tools                     提供[—]
  13. shell-tools      装 shell_tools      需要[tools, shell]
      dugentx.tools.shell_tools                  提供[—]
  14. subagent-tools   装 subagent-tools   需要[tools, subagents]
      dugentx.tools.subagent_tools               提供[—]
  15. self-extension   装 selfExtension    需要[tools]
      dugentx.plugins.self_extension             提供[dynamicPlugins]
  16. word-count       装 word-count       需要[tools, prompt]
      examples.plugins.word_count                提供[wordCount]

可用服务：agent、agentLoop、agents、approval、compaction、dynamicPlugins、fs、llm、models、permissions、prompt、session、sessionStore、shell、subagents、tools、wordCount
```

第二件事：模型能不能调到它。`dugentx run` 会把每一轮的关键事件打出来：

```console
$ uv run dugentx -c examples/plugins/demo.yml run "数一下这句话里有哪些词"
  · step 1
    → count_words({"text": "the quick brown fox jumps over the lazy dog, and the dog barks"})
    ✓ count_words: 共 13 个词/字，去重后 10 个
  · step 2

数完了。这句话里 the 出现最多，一共 3 次——这就是 count_words 工具干的事，它是 examples/plugins/word_count.py 挂上来的。

[completed · 2 步 · 1 次工具调用 · 0 tokens]
```

`→` 是一次工具请求，`✓` 是工具真的跑完并把结果回填了。这条链上没有模型：
`llm` 行换成了回放适配器，而工具、权限、日志、提示词拼装全是真的。

---

## 4. 让模型能调用你的工具

### `Tool` 是什么

```python
@dataclass(slots=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]          # JSON schema
    handler: Callable[..., Any]
    labels: frozenset[str] = frozenset()
    title: str = ""
```

`Tool.spec()` 把它翻成 OpenAI 的 `{"type": "function", "function": {...}}` 形式——
模型看到的就是这个，不是你的 Python 签名。

拿到 `Tool` 有两条路：手写一个 `Tool(...)`，或者让签名长出来（推荐）：

```python
tool_from_function(fn, *, name=None, labels=frozenset(), description=None)
```

它对函数有要求，缺一样就在这里报错，而不是等模型调到它才发现描述是空的：

- 每个参数都要有类型标注，否则 `工具 'f' 的参数 'x' 没有类型标注；模型要从标注生成 schema，缺了它就只能靠猜`；
- 有 docstring；`Args:` 之前的部分成为 `description`，`Args:` 段成为参数说明；
- 名字叫 `ctx` 或 `call` 的参数会被跳过（不打进 schema），执行时按名字注入。

`_json_schema` 只翻译最常见的那几种：`str` / `int` / `float` / `bool`、`X | None`、
`list[str]` 这类容器、枚举。遇到它不认识的标注会**退化成 `string`**，并在描述里说明——
一个工具描述得不够精确，不该让整个 harness 装不起来。

一个工具不做参数校验也没关系：模型给的 JSON 不保证合法，缺参会变成 `TypeError`，
被管道接住，作为一条 tool 结果回给模型——`ToolOutcome(ok=False, note="异常已捕获，已作为结果回传给模型")`。
它拿到「缺 path 参数」比拿到一个被填了默认值的调用有用得多。

### 标签：工具唯一的策略表达

```python
LABEL_READ = "read"
LABEL_WRITE = "write"
LABEL_DANGEROUS = "dangerous"
LABEL_NETWORK = "network"
```

标签**不**决定能不能跑，它只描述这个工具会造成什么。决定权在 `permissions` 那一行配置：

| 标签 | 默认档位 | 意思 |
|---|---|---|
| `read` | `auto` | 直接放行 |
| `write` | `confirm` | 先问一句 |
| `network` | `confirm` | 先问一句 |
| `dangerous` | `deny` | 默认拒绝 |
| 没有标签 | `default`（`confirm`） | 按「要问一句」算 |

工具同时带多个标签时取**最严**的那一档：`level_for` 用 `max(..., key=severity)`，
所以 `{read, write}` 按 `write` 算。这也是为什么 `min_length` 之外还留了一个 `labels` 配置项——
`examples/plugins/demo.yml` 里写的是 `labels: [read]`（`auto`，连审批都不问），改成 `[write]`
之后每次调用都要过一道审批：在那份配置里 `mode: policy` 会自动放行，换成 `mode: channel`
就真的弹到人面前。两种情况下工具代码都一个字不用改。

### 权限是怎么装上去的

`permissions` 插件（`dugentx/seams/permissions.py::install_gate`）往 `tools/pre-execute`
上挂了一个监听器，用 `prepend=True` 抢到最外层：

```python
async def gate(call: ToolCall, nxt):
    registry = ctx.service("tools")
    try:
        tool = registry.get(call.name)
    except Exception:
        return await nxt()            # 不认识的工具不归权限管，让下游去报「没有这个工具」
    level = policy.level_for(tool.labels)
    ...
    if level == "auto":
        ctx.events.emit("permission/skip", request)
        return await nxt()
    decision = await approval.decide(request)
    ctx.events.emit("permission/decided", request, decision)
    if not decision.allowed:
        raise PermissionDenied(decision.reason or f"{call.name} 被权限策略拒绝（{level}）")
    return await nxt()
```

被拒的那次调用不会往上抛异常，而是变成一条结果回到模型面前：
`ToolOutcome(content=str(exc), ok=False, blocked=True, note=f"标签={sorted(tool.labels)}")`。
所以模型能看见「谁拦的、按哪个标签拦的」。整条管道是：

```
tool/call                        （通知）
  tools/pre-execute   waterfall  ← 权限门挂在这里
    tools/execute     waterfall  ← terminal 是工具自己的处理器
  tools/post-execute  waterfall  ← 结果后处理
tool/result                      （通知）
```

**工具不知道权限存在。** `read_file` 里没有一行在判断自己该不该跑。

### 给自己的工具选标签

`word_count` 打的是 `read`：它只读你交给它的那个字符串，不改任何东西，也不碰网络。
一个诚实的例子是仓库里的 `run_command`，它只打 `write`——理由写在
`dugentx/tools/shell_tools.py` 的开头：标签是**构造时**固定的，而「这条命令危不危险」
取决于命令文本，构造期根本看不到。所以它不去注册两个工具
（`run_command` 和 `run_dangerous_command`），因为那等于给模型一个绕开约束的选项；
它把判定交给两个看得见命令文本的地方：`ShellPolicy` 的黑名单/白名单，以及审批层的
`argument_rules`（配置里写 `{tool: run_command, contains: "rm -rf", allowed: false}`）。

---

## 5. 加进配置，然后确认没弄坏顺序

把插件加进任何一份配置，就是加一行：

```yaml
plugins:
  - id: word-count
    plugin: examples.plugins.word_count
    config:
      min_length: 3
      top: 3
      labels: [read]
```

顺序不用管，也不该管。但**要确认它排到了哪儿**，而唯一可靠的确认方式是装一遍：

```bash
uv run dugentx -c examples/plugins/demo.yml plugins
```

这条命令不是「读配置算一下」，它真的 `boot()` + `start()` 了一遍，所以：

- 装得上才会打印（第 3.6 节那份输出）；
- 装不上时你看到的就是那句错误，而不是一张图；
- `按装载顺序` 一行是 `Composition.describe()` 的输出，`需要[...]` 和 `提供[...]` 直接从
  `plugin.inject` / `plugin.provides` 取——**你声明的就是装载器看到的**。

怎么读这份输出：`word-count` 排在 16，在所有它 `inject` 的键（`tools` 在 1、`prompt` 在 3）之后。

它在 16 这个位置，一半靠依赖、一半靠行序：`extends` 把基座的 15 行放在前面，这一行追加在最后，
而拓扑排序在依赖已经满足时保留行序，所以它留在末尾。**行序只在并列时起作用**——
换一份不 `extends` 的配置，把这一行写在最前面，它照样会被提到依赖之后
（下面这份 `examples/plugins/_order.yml` 是临时的，跑完删了）：

```console
$ cat examples/plugins/_order.yml
plugins:
  - id: word-count
    plugin: examples.plugins.word_count
  - id: tools
    plugin: dugentx.plugins.tools
  - id: prompt
    plugin: dugentx.plugins.prompt

$ uv run dugentx -c examples/plugins/_order.yml plugins
配置：examples/plugins/_order.yml
工作目录：C:\Users\Frees\Documents\Obsdian\Working\Agent学习\DugentX

按装载顺序：
   1. tools            装 tools            需要[—]
      dugentx.plugins.tools                      提供[tools]
   2. prompt           装 prompt           需要[tools]
      dugentx.plugins.prompt                     提供[prompt]
   3. word-count       装 word-count       需要[tools, prompt]
      examples.plugins.word_count                提供[wordCount]

可用服务：prompt、tools、wordCount
```

行序是 `[word-count, tools, prompt]`，装出来是 `[tools, prompt, word-count]`——
顺序是算出来的，不是你碰巧写对的。

可用服务那一行也要看一眼。`wordCount` 在列表里，说明 `provide` 真的执行了；
如果写的是 `ctx.provide` 之外的方式，它就会缺席——而那种缺席不会报错，只会让工具在第一次
被调用时抛 `ServiceNotFound`。

---

## 6. 把它做成一个包：一个能被发现的通用接口

到这里你的插件是一条**模块路径**（`examples.plugins.word_count`）：装载器 import 它、取它的 `create`。
这条路一点没变，而且对仓库里的东西是对的——模块跟着仓库走，`uv sync` 之后就能用。

但要把插件**交给别人**时它就不够了：对方得有一份你的代码、摆在同一棵树里、还得能被 import。
插件包解决的是这件事：它是一个正常的 Python 发行包，`pip install` 之后 DugentX 自己会看见它。

### 6.1 一个插件包需要什么

两样：一把被 `define_plugin` 装饰过的工厂，和 `pyproject.toml` 里的一条 entry point。

```toml
[project.entry-points."dugentx.plugins"]
clock = "dugentx_plugin_clock:PLUGIN"
```

组名 `dugentx.plugins` 是**对外契约**（`dugentx/kernel/registry.py` 里的 `PLUGIN_GROUP`），
改了就是破坏性变更。等号左边是插件在注册表里的名字——**配置里写的就是它**；右边是 `模块:属性`，
指向那把工厂。再加上一份能把模块打进 wheel 的 `[build-system]`，它就是一个可安装的发行包；
最小完整例子是 `examples/dugentx-plugin-clock/pyproject.toml`。

发现走的是标准 entry point，所以在装载器眼里，「写一个插件包」和「让 DugentX 看见它」是同一件事：
装完它就在注册表里了。

### 6.2 清单：不执行它，也能问它是干什么的

`define_plugin` 现在多一个 `version=` 参数：

```python
@define_plugin(
    "clock",
    inject=("tools", "prompt"),
    provides=("clock",),
    description="把当前时间告诉模型：一个 now 工具 + 一句提示词",
    version="0.1.0",                      # 插件自己的版本
)
def create(ctx: Context, config: dict[str, Any] | None = None) -> None: ...
```

装饰器把一份 `PluginManifest` 挂在**工厂函数**上（属性名 `__dugentx_manifest__`）：

| 字段 | 含义 |
|---|---|
| `name` / `description` | 插件名与一句话说明，来自 `define_plugin` 的参数 |
| `version` | 插件自己的版本，**你负责**，harness 只负责显示（默认 `"0.0.0"`） |
| `api_version` | 它按哪一版插件接口写的，默认 `PLUGIN_API_VERSION`（当前是 `"1"`） |
| `inject` / `provides` | 与 `Plugin` 上那两个字段同源，不会分叉 |
| `target` | `包.模块:属性`，装载器照着它去拿工厂 |
| `distribution` | 它来自哪个已安装的发行包；内置插件留空 |

它挂在工厂上、而不是工厂执行出来的，这一点就是全部意义：**读清单不需要调用工厂**，
也就是不需要执行插件的代码。于是「装了些什么、各自要什么、对不对得上这个版本」在装载之前就是可问的。
`manifest_of(candidate)` 负责取它，取不到返回 `None`。

`None` 不是错误：**没有清单的插件照样能装**。否则每加一个元数据字段，都等于把所有已存在的插件
判了死刑。一个没有清单的包也会被列出来，但我们只能填上从 entry point 上知道的那些（名字、
`模块:属性`、发行包名），版本显示 `0.0.0`，说明写成「（这个插件没有声明清单）」。

### 6.3 发现期检查什么，装载期检查什么

| 时刻 | 做了什么 | 需要什么 |
|---|---|---|
| `dugentx plugins --available` | 读 entry point → import 那个模块 → 读清单 | **都不用**：不读配置、不装载、不需要 key |
| 装载这份配置 | `resolve_factory` → 包名走注册表，**接口版本在这一步校验** | 包已经装在环境里 |
| mount 那一行 | 调工厂拿到 `Plugin` → 校验 `inject` 都有人提供 → 跑 `create`，注册真的发生 | 依赖的服务，以及配置里声明的 key |

接口版本对不上时抛的是 `PluginError`，报的是**版本**而不是插件里某一行 `AttributeError`
（源码原话）：

```
插件包 'future' 声明的是插件接口 v99，本机这个 harness 是 v1。要么升级这个插件包，要么退回对应版本的 DugentX。
```

### 6.4 怎么知道成了

第一件事，清单读得出来（这一条不读配置、不需要 key）：

```console
$ uv run dugentx plugins --available
已安装的插件包（1 个）：
  clock 0.1.0  需要[tools, prompt]  提供[clock]
      dugentx_plugin_clock:PLUGIN  来自 dugentx-plugin-clock
```

第一行是清单的 `describe()`（名字、版本、要什么、给什么），第二行是 entry point 的目标
加上发行包名。

第二件事，配置里写**包名**就能装上。`examples/plugin-package.yml` 除了一段注释，可执行的只有这些
（注释解释的正是「这里为什么写包名」）：

```yaml
extends: ../dugentx.replay.yml

plugins:
  - id: clock
    plugin: clock
    config:
      zone: 本机时区
```

```console
$ uv run dugentx -c examples/plugin-package.yml plugins
配置：examples/plugin-package.yml
工作目录：C:\Users\Frees\Documents\Obsdian\Working\Agent学习\DugentX

按装载顺序：
   …（1 到 15 行是仓库自带的那批插件）
  16. clock            装 clock            需要[tools, prompt]
      clock                                      提供[clock]

可用服务：agent、agentLoop、agents、approval、clock、compaction、dynamicPlugins、fs、llm、models、permissions、prompt、session、sessionStore、shell、subagents、tools
```

`config:` 那一行也照常校验：这个包只认 `hint` 和 `zone`，写别的键当场报
`clock 配置里有不认识的键：[…]；可用：['hint', 'zone']`。

第三件事，工具真的能用。回放脚本里调一次 `now`——临时配置，用绝对路径 `extends` 那份示例，
跑完删了：

```console
$ cat _clock_call.yml            # 只把 llm 那一行换成会调 now 的脚本
extends: <仓库根>/examples/plugin-package.yml

patch:
  - id: llm
    config:
      adapter: replay
      replay:
        turns:
          - - tool_call: now
              arguments:
                what: 星期几
          - - text: "clock 包挂上来的 now 工具真的执行了。"

$ uv run dugentx -c _clock_call.yml run "现在几点，星期几"
  · step 1
    → now({"what": "星期几"})
    ✓ now: 现在本地时间是 2026-09-14 19:00:32（本机时区），星期一
  · step 2

clock 包挂上来的 now 工具真的执行了。

[completed · 2 步 · 1 次工具调用 · 0 tokens]
```

`→` 是模型请求，`✓` 是插件包挂上来的那个工具真的跑完并回填了结果。括号里的「本机时区」来自配置行
`zone:`——配置真的走到了插件里。

### 6.5 读一遍 clock

`examples/dugentx-plugin-clock/dugentx_plugin_clock/__init__.py` 能看到插件包的全部要点：

- **`PLUGIN = create`。** entry point 指的就是它。取个显式名字是为了让 `pyproject.toml` 那行读起来
  没有歧义：指向的是**插件工厂**，不是这个模块里随便哪个新建的函数。
- **三笔注册都走 effect。** 一个 `clock` 服务、一个 `now` 工具、一段提示词段落。所以拔掉这个包，
  工具和提示词一起消失——「注册必须能撤销」对第三方包和对内核是同一条规则。
- **时间做成服务，而不是直接写进工具里。** 审计日志要盖时间戳、压缩要判断会话有多旧、模型要回答
  「现在几点」——它们该读同一个来源，而不是各自去 `datetime.now()`。
- **配置在装载时校验。** `KNOWN_CONFIG = {"hint", "zone"}`，多一个键就报错，而不是安静地不生效。

**为什么它的 `pyproject.toml` 里没有 `dependencies = ["dugentx"]`。** 真实发布出去的插件包应该写这一行。
这个示例故意省略：它是从本仓库**以路径方式**（`uv sync --extra dev`）装进来的，本地没有一份可解析的
`dugentx` 发行包，写了这一行反而会让本地安装失败。省略换来的是一个装着玩的示例，
**代价是它不能独立分发**——这一点写在那份文件的注释里，别当成模板照抄。

### 6.6 包名还是模块路径

`plugin:` 一个键、两种东西，判据在 `resolve_factory`（`dugentx/kernel/registry.py`）：

1. **先当已安装的插件包名查注册表。** 查得到就用它，不管这个名字长什么样。
2. 查不到，再按模块路径 import：`包.模块`，或 `包.模块:属性`（`:` 后面的属性缺省是 `create`）。

顺序不能反过来按「有没有点」判断：`module:attr` 里的 module 完全可以是个不带点的顶层模块名
（`my_module:create` 这种写法到处都是），拿字符串形状当判据一定会猜错。所以一个已安装的包名
哪怕长成 `dugentx.plugins.shell`，赢的也是注册表里那一条。

两条都解析不出来时报的是（源码原话，本机装着 `clock` 时的真实输出）：

```
'nosuchplugin' 既不是已安装的插件包，也不像模块路径（模块路径至少要有一个 `.` 或一个 `:`）。已安装的插件包有：clock
```

什么时候用哪个：

| 形态 | 配置里写 | 适合 |
|---|---|---|
| 模块路径 | `examples.plugins.word_count` | 跟着这个仓库走的、一次性的东西 |
| 插件包 | `clock` | 要给别人装的、要独立版本的、会和 harness 分开演进的东西 |

两种都支持是有意的：**包是给人分发的，模块是给自己用的。** 仓库自己那十几个插件走的还是模块路径。
另外，`dugentx/kernel/loader.py` 里的 `resolve_factory` 现在只是 `kernel/registry.py` 那一个的再导出，
所以 `from dugentx.kernel.loader import resolve_factory` 这类老写法照旧能用。

---

## 7. 三个常见的坑

### 坑一：注册不走 effect，卸载留下幽灵

```python
# 错的：注册完成了，但没有任何人记住怎么撤销它
def create(ctx: Context, config: dict) -> None:
    ctx.service("tools").register(tool_from_function(count_words))

# 对的：返回值交给作用域
def create(ctx: Context, config: dict) -> None:
    ctx.effect(lambda inner: inner.service("tools").register(tool_from_function(count_words)),
               label="tool:count_words")
```

**谁抓得住它**：`tests/test_kernel.py::test_a_plugin_that_bypasses_ctx_leaves_ghosts`。
它装一个「直接调注册接口」的插件，卸载之后断言工具还在表里——幽灵不是猜测，是可执行的证据。
运行时不会报任何错，所以这个坑只有在**卸载**那一刻才暴露：模型还看得见那个工具，
调用它却查不到背后的服务。

同一个道理适用于运行期动态挂载：`manage_plugin(action="unmount")` 之所以能保证「它注册过的
东西全部撤销了」，前提是那个插件的每一笔注册都走了那三条入口。这条规矩对动态机制自己也成立——
`self_extension` 卸载时会把自己挂上来的一起拔掉。

### 坑二：依赖没声明，或者声明了却没人提供

两种写法的区别只有一行 `inject`，但结果完全不一样。我做过这个实验（临时文件，跑完删了）。

一个不声明依赖、直接取 `prompt` 的插件，排在最前面时：

```console
$ uv run dugentx -c examples/plugins/_trap_first.yml plugins
dugentx: 上下文里没有服务 'prompt'；当前可用：（空）
```

这就是 `Context.service()` 的行为：拿不到就抛 `ServiceNotFound` 并列出可用的键，
**不回退到默认实现**——回退会让「我明明配了那个插件，怎么没生效」变成一个查不出来的问题。

同一个插件，如果那一行碰巧排在 `prompt` 之后（比如它是 `extends` 追加的末尾一行），
它会**装成功**，工具和提示词都挂上了。看起来没问题，只是顺序是碰巧对的。

```python
# 声明了：装载器把它排在 prompt 之后，这是算出来的
@define_plugin("word-count", inject=("tools", "prompt"), ...)

# 没声明：能不能装上取决于行序
@define_plugin("word-count", ...)
```

**谁抓得住它**：都发生在装载时，不会拖到运行期。声明了依赖但**没人提供**时，
装载器的第三道校验会当场报：

```
插件 'x' 需要服务 'weather'，但配置里没有任何一行声明提供它。当前能提供的是：[…]
```

运行期动态挂载时还有一道重复检查（`Context.mount`）：`插件 'x' 需要 ['prompt']，但装载时还没有；当前可用：[…]`。

两句话记住这个坑：**能装上不等于装对了**；顺序对是靠 `inject` 算出来的，不是靠行排得好。

### 坑三：事件名写错，注册成功、永远不会触发

```python
ctx.on("tool/calls", handler)     # 目录里叫 tool/call
```

`EventBus.on()` **不校验**事件名——它只是把监听器放进一个桶里。校验发生在**派发**时，
每个派发方法都会先跑 `_check(name, mode)`。所以上面那一行的后果是：

```console
$ uv run python _probe_events.py
listeners(tool/calls) = 1
收到的通知 = ['real']
emit 一个不在目录里的名字 → DuGentXError: 事件 'tool/calls' 不在事件目录里。写错事件名不会有任何提示，所以这里直接拦住。要新增事件，先写进 dugentx/events.py。
```

监听器**注册成功**（桶里确实有一个），一次 `count_words` 调用触发的是 `tool/call`，
所以拼错的那个从头到尾没响过，而没有任何地方会告诉你这件事。写错派发方式也一样：
用 `emit` 发一个声明为 `waterfall` 的事件，会当场报「声明的派发方式是 waterfall，却用 emit 发了出去」。

**谁抓得住它**：只有你自己，办法是查目录：

```bash
uv run dugentx events        # 生成自 dugentx/events.py，不要手改那个文件
```

（`docs/events.md` 是同一份目录的文档版，由 `scripts/gen_docs.py` 生成。）

在这个坑旁边还有一件事：挂在 `waterfall` 事件上的监听器签名是 `async def f(payload, nxt)`，
**忘了调 `nxt()` 就是短路**——下游和真正干活的 `terminal` 都不会跑。少了 `nxt` 也不一定报错：
`ToolRegistry.execute` 只在瀑布返回后检查类型（`tools/pre-execute 返回了 str，应当是 ToolOutcome`），
所以一个「不调 `nxt()`、老老实实返回 `ToolOutcome`」的监听器会**安静地吃掉这次工具调用**。
要拦就显式地拦（像权限门那样 raise / 返回自己的 outcome），要放行就 `return await nxt()`。

---

## 下一步

- 换一个缝试试：给模型加一只 `web_fetch`（打 `network` 标签，看它自己从 `tools/pre-execute`
  上长出权限），或者写一个新的 `Compactor` 实现（`dugentx/seams/compaction.py` 里的协议）。
- 想换掉的是**服务**而不是加一个：那一行的 `plugin:` 指向你的模块就行。循环本身也是服务
  （`ctx.agentLoop`），并行工具调用是换一个实现，不是改 `agent_loop_basic.py`。
- 你的插件在别人手里会怎样：第 6 节把插件做成一个可安装的包，
  `examples/dugentx-plugin-clock/` 就是那份能跑的模板；配置侧对 `plugin:` 的完整规则在
  `docs/configuration.md`。`docs/seams.md` 列了每条缝有什么方法、可以被谁换掉。
