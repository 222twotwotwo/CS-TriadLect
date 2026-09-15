# 配置：一份 YAML 变成一棵插件树

配置只声明两件事：**装了哪些插件**，以及**每一行自己的配置**。
它不声明顺序——顺序由每一行声明的依赖算出来。

装载分四步（`dugentx/kernel/loader.py`），任何一步失败都**在装载时**报错，
不留到运行期：

1. 读 YAML，连同它 `extends` 的基座，按 id 应用 `patch`；
2. 把每一行的 `plugin` 解析成一个工厂（`resolve_factory`）；
3. 校验：每一行 `inject` 的服务，必须有人 `provides`；
4. 按 `inject → provides` 拓扑排序，再按顺序实例化。

`AgentRuntime.boot()` 只做前三步和排序；`start()` 才真正 mount。
所以 `dugentx plugins` 是**装一遍再打印**，装不上时那句错误本身就是你要看的东西。

---

## 顶层只有三个键

```yaml
extends: dugentx.yml     # 可选：先读这份基座
plugins: []              # 一行一个插件
patch: []                # 按 id 覆盖上面的行
```

`_read_document()` 读完整份 YAML 之后，装载器只看这三个键。**其余顶层键一个都不读**：
没有顶层的 `model:`，也没有顶层的 `provider:`——它们属于某一行 `config`
（模型那三个键在 `llm` 行的 `config` 里）。于是也不存在「子配置的顶层键覆盖父配置的
顶层键」这回事：能被继承的只有 `plugins` 和 `patch`。

顶层不是映射（比如一份 YAML 列表）会报：

```
<路径> 的顶层必须是一个映射，收到 list
```

## 一行插件

| 键 | 必填 | 干什么 |
|---|---|---|
| `id` | 是 | 这一行的名字。`patch` 按它定位，`dugentx plugins` 按它排版 |
| `plugin` | 是 | 插件包名，或插件模块：`包.模块` / `包.模块:属性` |
| `config` | 否 | 传给插件工厂的映射，默认 `{}` |
| `disabled` | 否 | `true` 时这一行被丢掉，**连模块都不会 import** |
| `inject` | 否 | 覆盖插件自带的 `inject`；运行期动态挂载用它 |

### `plugin:` 怎么解析：包名还是模块路径

`resolve_factory`（`dugentx/kernel/registry.py`）按这个顺序解析：

1. **先当已安装的插件包名查注册表。** 查得到就用它——不管这个名字长什么样。
2. 查不到，再按模块路径 import：`包.模块`，或 `包.模块:属性`（`:` 后面的属性缺省是 `create`）。

```yaml
plugin: clock                        # 已安装的插件包，走 entry point 注册表
plugin: dugentx.plugins.fs           # 仓库里的模块，import 它，取它的 create
plugin: dugentx.plugins.fs:create    # 取指定的那个属性
```

顺序**不**按「字符串里有没有点」判断：`module:attr` 里的 module 完全可以是个不带点的顶层模块名
（`my_module:create` 这种写法到处都是），拿字符串形状当判据一定会猜错。判据是「注册表里有没有
这个名字」——所以一个已安装的包名哪怕长成 `dugentx.plugins.shell`，赢的也是注册表里那一条。

两条都解析不出来时（源码原话，本机装着 `clock` 时的真实输出）：

```
'nosuchplugin' 既不是已安装的插件包，也不像模块路径（模块路径至少要有一个 `.` 或一个 `:`）。已安装的插件包有：clock
```

模块路径解析失败时是这几句（源码原话）：

```
找不到插件模块 'dugentx.plugins.nope'：No module named 'dugentx.plugins.nope'
插件模块 'dugentx.plugins.fs' 既没有 `create`，也没在配置里指出 :属性 名
```

不管走哪条路，工厂都必须返回 `Plugin`（`@define_plugin` 装饰过的函数就是工厂）：

```
dugentx: 插件 'examples.plugins.x' 的工厂返回了 dict，不是 Plugin；插件模块要用 @define_plugin 装饰工厂函数
```

插件包怎么用 entry point 注册自己、清单是什么、接口版本在哪一步校验，见
`docs/writing-a-plugin.md` 第 6 节。

### 只看装了哪些插件包：`dugentx plugins --available`

`dugentx plugins` 回答的是「**这份配置**装出来是什么样」——它真的装载一遍，所以配置里声明的密钥
必须就位（缺 key 时长什么样，见下面「模型与密钥」一节）。`--available` 回答的是另一件事：
**这台机器上装了哪些插件包**。它只读注册表和清单，不读配置、不装载、不需要 key（也因此不需要 `-c`）：

```console
$ uv run dugentx plugins --available
已安装的插件包（1 个）：
  clock 0.1.0  需要[tools, prompt]  提供[clock]
      dugentx_plugin_clock:PLUGIN  来自 dugentx-plugin-clock
```

每一条是三段：清单的 `describe()`（名字、插件自己的版本、要什么、给什么）、entry point 的目标、
来自哪个发行包。

**退出码**：全部兼容时 0；只要有一个包声明的插件接口版本和本机对不上就是 1，并多打一行
「其中 N 个的插件接口版本和本机对不上，装载它们会失败。」，那一条长成
`名字 版本  ⚠ 接口版本 99，本机是 1`。本机装的 `clock` 是兼容的，所以正常的退出码是 0；
版本对不上那条分支是用一个注入了假 entry 的探针跑出来的，不是本机的真实安装。

插件自己的版本在 `define_plugin` 上声明：`@define_plugin("clock", …, version="0.1.0")`。
它是**插件作者**负责的字段，harness 只负责显示；省略就是 `0.0.0`。

---

## `extends`：一层叠一份

`extends` 是 dsh「profile → bundle → patch」三层叠法的一层化版本。真正发生的事只有三行
（`_collect_rows` 的返回值）：

```python
return [*base_rows, *own_rows], patches     # 基座的行先来，自己的行追加在后面
patches = [*base_patches, *own_patches]     # 自己的 patch 在基座之后应用
```

所以叠出来的顺序是：

| 顺序 | 内容 | 谁赢 |
|---|---|---|
| 1 | 基座的 `plugins` 行 | 先装 |
| 2 | 自己的 `plugins` 行 | 追加在后面 |
| 3 | 基座的 `patch` | 先应用 |
| 4 | 自己的 `patch` | 后应用，**同一个 id 上它赢** |

四条规则，都是源码里的原话：

- **相对路径按这份配置文件所在目录解析**：`parent_path = (path.parent / str(parent)).resolve()`。
  所以 `examples/plugins/demo.yml` 里写 `../../dugentx.replay.yml` 指向仓库根那一份；
  而在同一个文件里写 `dugentx.replay.yml` 会报
  `examples\plugins\_typo.yml 的 extends 指向了不存在的文件：dugentx.replay.yml`。
- **只解析一条链，链上没有文件就报错**：
  `examples/plugins/x.yml 的 extends 指向了不存在的文件：nowhere.yml`。
- **拒绝成环**。真实输出（三个文件互相指）：
  ```
  dugentx: 配置 extends 成环：_a.yml → _c.yml → _b.yml → _a.yml
  ```
  括号里的顺序是递归栈，从最外层那份开始；环里的每个文件名都会出现。
- **深度上限 8 层**：`配置 extends 太深（超过 8 层）：<路径>`。一层 `extends` 已经够表达
  「换一个人机通道」，这个上限只是防呆。

### 子配置能关掉、能覆盖父配置的哪一行吗

能，**唯一的方式是 `patch`**：

```yaml
extends: dugentx.yml
patch:
  - id: human
    disabled: true      # 关掉父配置里的那一行
```

**在 `plugins:` 里再写一行同 id 不会覆盖父行。** id 不参与去重，装载器只看见两行，
两行都会装。真发生了会在装载时以另一种形式暴露出来，比如：

```
dugentx: 工具 'run_command' 已经注册过了；工具名必须唯一
```

或者两行都 `provides` 同一个键时：

```
dugentx: 服务 'fs' 被两个插件同时声明提供：'fs' 和 'fs-again'。同一个键只能有一个提供者。
```

### `model` 和别的顶层键，哪个文件赢

**都不赢，因为没有这回事。** 顶层不读 `model`。想换模型就 `patch` 那一行 `llm` 的 `config`：

```yaml
patch:
  - id: llm
    config:
      adapter: anyllm
      model: deepseek-v4-pro
      provider: deepseek
      api_key_env: DEEPSEEK_API_KEY
```

注意这是**整份 config 替换**（见下一节），所以原来 `llm` 行 config 里你还想留的键要重写一遍。

---

## `patch`：整行 config 替换，不是深合并

`apply_patches` 认识的就是一行能写的那些键，其中 `id` 必填：

| patch 键 | 作用 |
|---|---|
| `id` | 必填。找得到同 id 的行就改它；**找不到就把这一项当成新的一行插到末尾** |
| `plugin` | 换实现。不写就保留原来的 |
| `config` | **整份替换**这一行的 config。想留的键要完整写一遍 |
| `disabled` | 关掉这一行 |
| `inject` | 覆盖这一行的依赖声明；不写就保留原来的 |

patch 项缺 `id` 会报：`<来源> 里的覆盖项缺少 id：{...}`。
用「不存在的 id」插入新行时，那一项必须同时有 `id` 和 `plugin`，否则按一行的规则报错。

为什么不深合并：深合并会让「删掉一个键」变成一件说不清楚的事——写 `config: {}`
到底是「什么都不改」还是「全删掉」？整份替换只有一个答案。

`dugentx.tui.yml` 是这条规则的真实用例（整份文件只有这些）：

```yaml
extends: dugentx.yml
patch:
  - id: human
    disabled: true          # stdio 通道和 TUI 通道提供同一个键（ctx.human），只能有一个
  - id: tui
    plugin: dugentx.plugins.tui
    config:
      prompt: "› "
      show_banner: true
      history_limit: 200
      color: null
```

命令行**没有** `-D key=value` 这种覆盖开关，`dugentx` 只认 `-c/--config`。
程序里的覆盖走 `AgentRuntime.boot(config, overrides=[...])`，语义与 `patch` 一样，
只是最后应用，来源记作「命令行覆盖」。`tests/test_tui_app.py` 用它保留真实配置、
只换掉 `llm` 那一行。

---

## 装载时会拦下什么

| 检查 | 在哪 | 报错（源码原话） |
|---|---|---|
| 每行有 `id` 和 `plugin` | `PluginRow.from_mapping` | `<文件> 的每一行都必须有 id 和 plugin；收到：{...}` |
| 工厂能解析 | `resolve_factory` | `找不到插件模块 'x'：No module named 'x'`；包名则是 `'x' 既不是已安装的插件包，也不像模块路径…` |
| 工厂返回 `Plugin` | `load_composition` | `插件 'x' 的工厂返回了 dict，不是 Plugin；…` |
| 一个键只有一个提供者 | `order_plugins` | `服务 'fs' 被两个插件同时声明提供：'a' 和 'b'。同一个键只能有一个提供者。` |
| `inject` 有人提供 | `order_plugins` | `插件 'x' 需要服务 'compaction'，但配置里没有任何一行声明提供它。当前能提供的是：[…]` |
| 依赖不成环 | `order_plugins` | `插件依赖成环：a → b → a` |
| mount 时依赖都就位 | `Context.mount` | `插件 'x' 需要 ['prompt']，但装载时还没有；当前可用：[…]` |

`Context.mount` 那道检查是**重复**的：装载器已经排过序了。留着它是因为运行期动态挂载
（`manage_plugin`）走的是 `mount`，而不是装载器。

报错的出口有两条：`DuGentXError` 家族由 CLI 接住，打印一行
`dugentx: <消息>` 并返回 2；其他异常会以栈回溯的形式出现、返回 1。
插件自己抛的异常**应该**是前者（`PluginError` 就是 `DuGentXError` 的子类），
所以你在插件里看到 `raise ValueError(...)` 时，那基本是漏改——`agent` 插件
曾经就是这样，后来被 `tests/test_agent_plugin.py` 钉住了。

---

## 全部插件

「行 id」是仓库自带配置里用的那个名字——`id` 是配置侧的自由标签，不是插件自带的。
括号里注明只在哪份配置里出现；没注明的在 `dugentx.yml` 和 `dugentx.replay.yml` 里都有。

| 行 id | 模块 | 提供 | 需要（`inject`） | 一句话 |
|---|---|---|---|---|
| `tools` | `dugentx.plugins.tools` | `tools` | — | 工具注册表 + 四段执行管道 |
| `session` | `dugentx.plugins.session` | `session`、`sessionStore` | — | 会话日志，回合结束时刷盘 |
| `prompt` | `dugentx.plugins.prompt` | `prompt` | `tools` | 系统提示词段落注册表 |
| `llm` | `dugentx.plugins.llm` | `llm`、`models` | — | 模型适配器注册表（anyllm / replay）+ 运行期换模型 |
| `agent-loop` | `dugentx.providers.agent_loop_basic` | `agentLoop` | `llm`、`tools`、`session` | 默认的 agent 循环 |
| `fs` | `dugentx.plugins.fs` | `fs` | — | 本地文件系统，路径锁在工作区根里 |
| `shell` | `dugentx.plugins.shell` | `shell` | — | 命令执行：超时、进程树击杀、黑白名单 |
| `human` | `dugentx.plugins.human` | `human` | — | stdio 人机通道（只在 `dugentx.yml`） |
| `tui` | `dugentx.plugins.tui` | `human`、`tui` | `agent` | 编码 TUI（只在 `dugentx.tui.yml`） |
| `permissions` | `dugentx.plugins.permissions` | `permissions`、`approval` | `tools` | 按标签分级，把门挂在 `tools/pre-execute` |
| `compaction` | `dugentx.plugins.compaction` | `compaction` | `session` | 超预算时压缩，并把决定写进日志 |
| `skill` | `dugentx.plugins.skill` | `skills` | — | 文件系统上的技能目录（只在 `dugentx.yml`） |
| `subagent` | `dugentx.plugins.subagent` | `subagents` | `agents` | 同进程的子 Agent |
| `agent` | `dugentx.plugins.agent` | `agents`、`agent` | `session`、`tools` | agent 名单与这次的主 agent |
| `fs-tools` | `dugentx.tools.fs_tools` | — | `tools`、`fs` | read_file / list_dir / search_text / write_file / edit_file |
| `shell-tools` | `dugentx.tools.shell_tools` | — | `tools`、`shell` | run_command |
| `skill-tools` | `dugentx.tools.skill_tools` | — | `tools`、`skills`、`session` | use_skill（只在 `dugentx.yml`） |
| `subagent-tools` | `dugentx.tools.subagent_tools` | — | `tools`、`subagents` | delegate_task |
| `ask-tools` | `dugentx.tools.ask_tools` | — | `tools` | ask_human（只在 `dugentx.yml`） |
| `self-extension` | `dugentx.plugins.self_extension` | `dynamicPlugins` | `tools` | 运行期挂载/卸载插件 + manage_plugin |

一共 20 个插件模块。`dugentx.yml` 装 19 行，`dugentx.replay.yml` 装 15 行
（少 `human`、`skill`、`skill-tools`、`ask-tools`），`dugentx.tui.yml` 在 `dugentx.yml`
的基础上加 `tui`、关 `human`。

这张表只列仓库自带的**模块**（`plugin:` 写模块路径的那些）。你从外面 `pip install` 的插件包
不在表里——它们由 `dugentx plugins --available` 列，配置里写包名即可（见上一节）。
仓库里那份能跑的插件包是 `examples/dugentx-plugin-clock/`，`examples/plugin-package.yml`
装的 `clock` 那一行就是这种写法。

## 每一行读哪些配置键

默认值一列是**省略该键时的行为**。写着「必填」的键缺了就在装载时报错，不会猜一个默认值。

| 模块 | 配置键（默认） | 说明 |
|---|---|---|
| `plugins.tools` | `description_style`（无） | 写 `compact` 时把工具描述压成第一行前 120 字符 |
| `plugins.session` | `directory`（`.dugentx/sessions`） | 相对**进程工作目录**解析，不是配置文件的位置 |
| | `session_id`（无） | 给了且文件存在就接着上次；否则开新会话 |
| `plugins.prompt` | `persona`（代码里的 `DEFAULT_PERSONA`） | 身份与工作方式那一段 |
| | `workspace`（无） | 有 `ctx.fs` 时以它的 root 为准，否则用这个值，再否则 `Path.cwd()` |
| | `extras`（`""`） | 部署方自己的规则，`order=90`，排在最后 |
| `plugins.llm` | `adapter`（`anyllm`） | 可选 `replay`；不认识的名字在装载时报错并列出可选项 |
| `plugins.llm`（anyllm） | `model`（必填） | 空字符串在构造适配器时报错 |
| | `provider`（必填） | 例如 `openai` / `deepseek` / `ollama` |
| | `temperature`（无） | 不设置就不传 |
| | `max_tokens`（无） | 不设置就不传 |
| | `reasoning_effort`（无） | 只在设置了才传 |
| | `api_key_env`（无） | 放**变量名**；留空交给 any-llm 按 provider 自己的约定变量去找 |
| `plugins.llm`（replay） | `turns`（`DEFAULT_TURNS` 的一句默认回复） | 外层是「轮」，内层是「这一轮里的若干增量」 |
| | `on_exhausted`（`error`） | 另一个取值是 `repeat`（重复最后一轮） |
| `providers.agent_loop_basic` | 无 | 给了也不读 |
| `plugins.fs` | `root`（必填） | 没有它宁可装载失败：路径策略需要基准 |
| | `extra_roots`（`[]`） | 额外允许的根 |
| | `max_bytes`（`1_000_000`） | 单次写入上限（UTF-8 字节） |
| | `read_max_bytes`（`2_000_000`） | 单次读取上限 |
| `plugins.shell` | `root`（`Path.cwd()`） | 命令的工作目录 |
| | `timeout`（`60.0`） | 默认超时（秒） |
| | `max_output_bytes`（`200_000`） | stdout / stderr 各自的上限 |
| | `allow_prefixes`（`()`） | 非空时**只放行这些前缀** |
| | `deny_substrings`（`()`） | 追加到默认黑名单上，不能替换掉它 |
| | `env`（`{}`） | 附加到子进程环境变量上 |
| `plugins.human` | `allow_always`（`True`） | 允不允许「本会话内都允许」这个选项；未知键报错 |
| `plugins.tui` | `prompt`（`"› "`） | 输入行前缀 |
| | `color`（`null`） | `null` 表示自动判断（重定向到文件时关掉） |
| | `show_banner`（`True`） | 开场那几行 |
| | `history_limit`（`200`） | 记多少条历史输入 |
| | `input_stream`（`sys.stdin`） | 测试用：换一个按键来源；未知键报错 |
| `plugins.permissions` | `default`（`confirm`） | 没有标签的工具算哪一档 |
| | `levels`（`read→auto`、`write→confirm`、`network→confirm`、`dangerous→deny`） | 标签到档位的覆盖 |
| | `mode`（`policy`） | `policy` / `interactive` / `channel` |
| | `answers`（`auto→true`、`confirm→false`、`deny→false`） | 只在 `policy` 模式下用；接受 `allow`/`refuse` 或 `true`/`false` |
| | `tool_overrides`（`{}`） | 按工具名的个别答案 |
| | `argument_rules`（`()`） | 按参数文本的规则，`{tool, contains, allowed, reason}` |
| `plugins.compaction` | `context_budget_tokens`（`60_000`） | 超过它才压 |
| | `prune_tool_results`（`True`） | 先剪工具结果 |
| | `keep_recent_tool_lines`（`12`） | 最近的工具结果留多少行 |
| | `summarize`（`True`） | 写摘要 |
| | `truncate`（`True`） | 还超就截断 |
| | `keep_recent_messages`（`6`） | 最少要留几条（至少 1） |
| | `target_ratio`（`0.6`） | 压到预算的多少（`(0, 1]`） |
| | `summarize_model`（`""`） | 空就用发起压缩的那个 agent 的模型 |
| | `summarize_chars`（`160`） | 机械摘要每条取多少字符 |
| | `carry_chars`（`2000`） | 摘要里尽量带上的原文量 |
| `plugins.skill` | `directories`（必填，非空列表） | 逐个 stat；目录不存在就在装载时报错 |
| `plugins.subagent` | `default_model`（`""`） | 空就用 `ctx.llm.default_model`（和主 agent 同一个兜底） |
| | `max_steps`（`16`） | 子 Agent 的熔断；请求只能更小，不能更大 |
| | `tool_allow`（`()`） | 子 Agent 的工具白名单；空表示不限制 |
| | `persist_sessions`（`True`） | 子会话要不要也落盘 |
| `plugins.agent` | `model`（`""`） | 空就用 `ctx.llm.default_model` |
| | `max_steps`（`24`） | 一个回合内最多几步（熔断，不是完成判断） |
| | `temperature`（`None`） | |
| | `reasoning_effort`（`None`） | |
| | `tool_allow`（`()`） | 非空即白名单 |
| | `tool_deny`（`()`） | |
| | `prompt_extras`（`{}`） | 给提示词段落读的键值（`from_config` 用） |
| | `context_budget_tokens`（`60_000`） | 和 compaction 的默认值一致 |
| | `label`（`"main"`） | 写进会话日志（`session/start` 的 `label`） |
| `tools.fs_tools` | 无 | 上限和忽略名单在工具与 fs 里 |
| `tools.shell_tools` | `dangerous`（`False`） | `True` 给 `run_command` 加上 `dangerous` 标签 |
| `tools.skill_tools` | 无 | 给了任何键都报错（技能目录配在 `skill` 那一行） |
| `tools.subagent_tools` | 无 | 同上（子 Agent 的配置在 `subagent` 那一行） |
| `tools.ask_tools` | `tool_name`（`ask_human`） | 改工具名 |
| `plugins.self_extension` | `tool_name`（`manage_plugin`） | 改工具名 |

**未知配置键的处理并不统一。** 报了「不认识的键」就会装载失败的是：
`human`、`tui`、`agent`、`compaction`、`skill`、`subagent`、`skill-tools`、
`subagent-tools`。只读自己认识的键、其余静默忽略的是：`tools`、`session`、`prompt`、
`llm`、`fs`、`shell`、`permissions`、`fs-tools`、`shell-tools`、`ask-tools`、
`self-extension`、`agent-loop`。这不是设计，是历史：报错的那批是后来加的。

`permissions` 是个中间态：它**不检查未知键**，但会检查它读到的那些键的**值**
（`mode` 只能是三个之一，`levels` 的值只能是 `auto`/`confirm`/`deny`，`answers`
只认 `allow`/`refuse` 或 `true`/`false`）。所以在它那里写
`levels: {writes: auto}` 不会报错——多出来的那一档永远不会被任何标签匹配到，
它只是安静地没生效；而写 `levels: {write: alow}` 会当场报错。

---

## 模型与密钥

模型这一层只有一行：`llm`。它的 `config` 里三个键决定「用哪家、哪个模型、key 从哪来」。

```yaml
- id: llm
  plugin: dugentx.plugins.llm
  config:
    adapter: anyllm
    model: deepseek-flash
    provider: deepseek
    api_key_env: DEEPSEEK_API_KEY
```

- `api_key_env` 里放的是**环境变量的名字**，不是 key 本身。配置会被打印、会被写进日志、
  会被提交进 git，key 不能出现在那里。
- 留空则交给 any-llm 用 provider 自己的约定变量去找（`OPENAI_API_KEY` 之类）。
- **变量缺失是装载错误，不是请求错误。** 设了 `api_key_env` 却取不到值，`plugins` 都装不起来：

  ```
  $ uv run dugentx -c dugentx.yml plugins        # 没有设 DEEPSEEK_API_KEY
  dugentx: 配置里指定了 api_key_env='DEEPSEEK_API_KEY'，但环境里没有这个变量（或它是空的）
  $ echo $?
  2
  ```

  这里的态度是：宁可装载失败，也不要等第一次请求被 provider 回一个 401——那时候错误信息
  会指向模型，而不是指向配置。
- 完全不要 key 的路是 `adapter: replay`（`dugentx.replay.yml`）：模型是脚本里的，
  工具、权限、日志、压缩全是真的。

### 运行期换模型：`ctx.models`

`llm` 那一行除了 `ctx.llm`，还提供 `ctx.models`（`dugentx/providers/model_switch.py` 的
`ModelSwitcher`）。所以「这个会话现在在跟谁说」是一件运行期可问、可改的事，不用重启、不用改配置行：

```python
ctx.models.current()                                   # 现在真正会被用到的 provider / model / adapter
ctx.models.providers()                                 # any-llm 认得的所有 provider 名
ctx.models.adapters()                                  # 已经注册在 ctx.llm 里的适配器名
ctx.models.switch(provider="openai", model="gpt-4o-mini")   # 换一个新的 anyllm 适配器
ctx.models.use("replay")                               # 切到一个**已经注册**的适配器，不新建
ctx.models.dispose()                                   # 把换出去的注册收回来（卸载时自动跑）
```

有一条不变量值得单独记住，因为踩上去时症状会指错方向：循环取的是
`agent.config.model or registry.default_model`（`_default_model()` 读的就是注册表上那个字段），
而 agent 的模型名通常来自配置里写死的那一行。
所以一次切换必须**同时**改这两处——只换适配器、不改模型名，结果是拿新 provider 去请求旧模型名，
报出来的错看着像「这家不支持这个模型」。这条由 `ModelSwitcher._install` 里那两行赋值保证。

`switch()` 的参数错（provider 空、model 空、key 变量不存在）在**构造适配器时**就抛出来，
和装载期校验同一个道理；抛错时什么都不改，不会留下半个状态。

一次切换会记两条：会话日志里一条 `model/switched`（字段 `provider` / `model` / `adapter`），
总线上一条同名事件。事后回看「当时到底问了谁」，唯一的出处就是日志那一条。

`providers()` 那份清单来自 any-llm，之所以要经由这个服务转一道，是因为
**只有 `dugentx/providers/llm_anyllm.py` 可以 import `any_llm`**——界面和插件都不能自己再 import 一遍。

---

## 常见改法

每一段都是能直接粘进配置的最小改动。改完跑 `uv run dugentx -c <你的配置> plugins`，
它会把顺序和可用服务都打出来。

### 换模型

```yaml
patch:
  - id: llm
    config:
      adapter: anyllm
      model: deepseek-v4-pro      # 只改这一行
      provider: deepseek
      api_key_env: DEEPSEEK_API_KEY
```

### 换 provider

```yaml
patch:
  - id: llm
    config:
      adapter: anyllm
      model: gpt-4o-mini
      provider: openai
      api_key_env: OPENAI_API_KEY   # 留空也行，any-llm 会按 provider 的约定变量找
```

走自建网关：`provider` 不变，把网关地址放进环境变量。每个 provider 在 any-llm 里各有一个
约定变量，名字不统一（deepseek 是 `DEEPSEEK_API_BASE`，openai 是 `OPENAI_BASE_URL`），
DugentX 自己的代码里没有一行读它——那是 any-llm 的事，所以 harness 一行不改。

### 完全离线跑

```yaml
patch:
  - id: llm
    config:
      adapter: replay
      replay:
        on_exhausted: repeat
        turns:
          - - text: "这次不用联网，也不用 key。"
```

### 关掉一个工具包

```yaml
patch:
  - id: shell-tools
    disabled: true
```

那一行被丢掉，连模块都不会 import；`run_command` 从模型眼前消失。循环、提示词、
权限策略一行都不用改——工具清单是提示词段落现取注册表拼出来的。

### 加一个插件

```yaml
plugins:
  - id: word-count
    plugin: examples.plugins.word_count
    config:
      min_length: 3
      labels: [read]
```

自己写的插件放进 `plugins:` 就行，顺序不用管（`inject` 会把它排到对的位置）。
完整例子见 `docs/writing-a-plugin.md`。

### 换人机通道

不要复制整份配置，用 `patch`。`dugentx.tui.yml` 整份文件就这些：

```yaml
extends: dugentx.yml
patch:
  - id: human
    disabled: true
  - id: tui
    plugin: dugentx.plugins.tui
```

`human` 和 `tui` 提供同一个键（`ctx.human`），所以必须关掉一个——一个进程里
同时有两张嘴问同一个人，只会把问题问乱。

### 改权限模式

```yaml
patch:
  - id: permissions
    config:
      mode: policy            # policy（按答案表） / interactive（终端上问） / channel（走 human 缝）
      levels:
        write: auto           # 写操作不再问
        dangerous: confirm    # 但能改自己工具箱的动作仍然要过一道闸
      answers:
        auto: allow
        confirm: allow
        deny: refuse
```

`mode: channel` 是给人用的那个：审批弹在装了 TUI 的那个终端里。
`mode: interactive` 在非终端环境（CI、管道）里一律拒绝，这是有意的——
一个在 CI 里挂住等输入的 harness 比一个当场说「不」的糟糕得多。

### 换掉整个循环

```yaml
patch:
  - id: agent-loop
    plugin: my_package.parallel_loop     # 提供 agentLoop，实现 AgentDriver 协议
```

循环是服务（`ctx.agentLoop`），所以它可以被整个换掉——并行工具调用、
人在环里的暂停恢复，都是换一个实现，而不是去改 `agent_loop_basic.py` 里的 if。

---

## 已知的不一致

写在最后，因为它会影响你排查问题的方式。

- **未知配置键有的报错、有的静默忽略**（上一节末尾那张名单）。写配置时要确认一个键
  到底有没有生效，唯一的办法是跑 `dugentx plugins` 看服务/工具，或者读那个插件文件。
  报错的那一批抛的都是 `PluginError`，所以缺 key、键名拼错这类问题都由 CLI 接住，
  出口统一是 `dugentx: …` 加退出码 2——不会留给你一段栈回溯。
- **`extends` 只有一层**，环和 8 层是硬上限。再多的层，配置会先于代码变得难懂。
- **行 id 不去重**。父配置已有 `id: fs`，子配置再写一行 `id: fs` 不会覆盖它，
  只会多装一个——通常表现为「服务已经被占用」或「工具已经注册过了」。
