# DugentX

一个可拔插的 Agent harness。用 Python 写，能读完，能跑起来。

招新 Agent 组的示例项目：它示范的不是「怎么调一次大模型」，而是**一家 harness 真正要做的那部分工作**——循环、工具管道、上下文、权限、压缩、子 Agent、运行期扩展。

---

## 两条硬规则

**第一条：LLM 层不自造。**

所有模型调用都走 [`any-llm`](https://github.com/mozilla-ai/any-llm)。整个仓库里**只有一个文件** import 它：

```
dugentx/providers/llm_anyllm.py
```

没有 `requests`，没有 `httpx`，没有一处手写的 HTTP 请求。理由不是洁癖：手写 HTTP 意味着你要自己处理重试、流式分片、各家不同的字段名、工具调用格式、错误码映射——这些工作没有一样是这家 harness 的独特价值，做错了却会以「模型今天不太行」的形式表现出来。

换 provider、换模型、换网关，改的是 `dugentx.yml`，不是代码。

**第二条：其他全部自造。**

循环、工具注册表与执行管道、会话日志、系统提示词拼装、文件系统、命令执行、权限分级、上下文压缩、子 Agent、技能加载——这些是 harness 的本体，一行都没有外包。

---

## 跑起来

```bash
uv sync --extra dev

# 什么 key 都不用：回放适配器，不联网，但工具真的执行
uv run dugentx -c dugentx.replay.yml run "读一下 hello.txt 然后告诉我里面写了什么"

# 看一遍编码 TUI（同样不需要 key）
uv run python examples/tui_demo.py

# 真跑。默认接 DeepSeek，key 只从环境变量读
export DEEPSEEK_API_KEY=sk-...
uv run dugentx tui                     # 编码 TUI
uv run dugentx run "看看这个目录里有什么"
```

换模型只是改 `dugentx.yml` 里 `llm` 那三行（`model` / `provider` / `api_key_env`）。
`deepseek-flash` 是默认——快、便宜、默认开思考链、上下文 1M，工具调用完整；
要更重的推理就换 `deepseek-v4-pro`。走自建网关设 `DEEPSEEK_API_BASE`。
换任何一家 provider 都不需要改 harness 一行代码。

几条用来「看」的命令：

```bash
# 这几条不需要 key
uv run dugentx -c dugentx.replay.yml plugins        # 这次装了哪些插件，按什么顺序
uv run dugentx plugins --available                  # 这台机器上装了哪些插件包（不读配置、不需要 key）
uv run dugentx events                               # 事件目录（纯目录，不装载任何东西）
uv run dugentx sessions                             # 本地会话日志（直接读配置）

uv run dugentx -c dugentx.replay.yml replay <session_id>   # 重新投影一个历史会话
uv run pytest -q                                    # 全部测试离线跑
```

**为什么要带 `-c dugentx.replay.yml`。** 不带 `-c` 时 `plugins` / `replay` 读默认的
`dugentx.yml`，而那份组合声明了 `api_key_env: DEEPSEEK_API_KEY`；缺 key 它会
**拒绝启动**。这是有意的：组合里写着要用的密钥不存在，就该在装载时大声说出来，
而不是等到第一次请求模型才炸。手头没有 key 又想看组合长什么样，就用回放配置——
它不声明任何密钥。

`replay` 这条命令值得单独试一次。它把「模型可见 ⟺ 已记录」从一句口号变成你随时能跑一下、看一眼的东西。

---

## 文档

这一篇是概览。更细的拆成了几篇，都在 [`docs/`](docs/)：

| | |
|---|---|
| [设计思路](docs/architecture.md) | 为什么内核是七个文件、为什么「注册必须能撤销」是全部 |
| [缝的参考](docs/seams.md) | 12 条缝的方法签名、谁实现、谁消费、怎么换掉 |
| [事件目录](docs/events.md) | 每个事件谁发、谁在听（**自动生成**，改了代码不同步就会失败） |
| [配置参考](docs/configuration.md) | 每个插件的配置项与默认值，`extends` 与 `patch` 的确切语义 |
| [写一个插件](docs/writing-a-plugin.md) | 一个完整例子，带你走一遍，附跑通的输出；模块与包两种形态 |
| [编码 TUI](docs/tui.md) | 那个终端界面是怎么**长出来的**、怎么启动、键位、边界 |

## 架构

### 内核只有七件东西

`dugentx/kernel/` 里那七个模块构成了「可拔插」本身，它们不知道 Agent 是什么：

| 模块 | 干什么 |
|---|---|
| `context.py` | 服务仓库。插件靠 `ctx.<key>` 拿服务，**从不 import 具体实现** |
| `plugin.py` | 插件 = 名字 + 要用的服务（`inject`）+ 一段 `apply`，外加挂在工厂上的那份清单 |
| `manifest.py` | 清单：一个插件**不执行也能被问**要什么、给什么、按哪版接口写的 |
| `registry.py` | meta 接口：已安装插件包的名单；`plugin:` 怎么解析也住在这里 |
| `effect.py` | 可撤销的副作用。每笔注册返回一个 disposer |
| `events.py` | 事件总线，五种派发方式 |
| `loader.py` | 读配置 → 校验 → 拓扑排序 → 实例化 |

`manifest.py` 与 `registry.py` 是后加的一层：它们回答「装了哪些插件、各自要什么」，
而**回答这些不需要执行插件的代码**。`dugentx plugins --available` 就是这一层的用处。
为什么它必须是「声明」而不是「执行结果」，见 [设计思路](docs/architecture.md)。

### 能力缝：三个角色，缺一不可

一个**完整的缝**有三个角色，这是 dsh 的划分方式，DugentX 照搬：

| 角色 | 是什么 | 在哪 |
|---|---|---|
| Service Definition | 接口与数据词汇 | `dugentx/seams/` |
| Service Provider | 具体实现 | `dugentx/providers/` |
| Consumer | 用它的东西，通常是模型可调用的工具 | `dugentx/tools/` |

只有接口没有实现是设计稿；只有实现没有消费者是死代码。

**12 个缝：**

| 缝 | 服务键 | 一句话 |
|---|---|---|
| `llm` | `ctx.llm` / `ctx.models` | 模型适配器注册表 + 运行期换模型；唯一允许依赖 any-llm 的地方 |
| `tools` | `ctx.tools` | 工具注册表 + 四段执行管道 |
| `agent` | `ctx.agents` / `ctx.agent` | 会话载体、回合与步的词汇 |
| `session` | `ctx.session` | 只能追加的事件日志，以及从它投影出的模型历史 |
| `prompt` | `ctx.prompt` | 系统提示词按段注册、按序拼装 |
| `fs` | `ctx.fs` | 文件访问 + 路径策略 |
| `shell` | `ctx.shell` | 命令执行，带超时与熔断 |
| `permissions` | `ctx.permissions` / `ctx.approval` | 三档权限，开关在代码侧 |
| `human` | `ctx.human` | 人机通道：终端、TUI、CI 自动回答都是它的实现 |
| `compaction` | `ctx.compaction` | 上下文预算满了怎么办 |
| `subagent` | `ctx.subagents` | 把一件事整个交出去 |
| `skill` | `ctx.skills` | 按需加载的说明文档 |

### 插件可以是模块，也可以是一个包

配置里那一行写的是 `plugin:`。它有两种写法，判据是**注册表里有没有这个名字**，
不是字符串里有没有点（`module:attr` 里的 module 完全可以是个不带点的顶层模块名）：

```yaml
plugin: dugentx.plugins.fs     # 仓库里的模块：import 它，取它的 create
plugin: clock                  # 已安装的插件包：走 entry point 注册表
```

插件包就是一个正常的 Python 发行包，用一条 entry point 声明自己是插件：

```toml
[project.entry-points."dugentx.plugins"]
clock = "dugentx_plugin_clock:PLUGIN"
```

被 `define_plugin` 装饰的那把工厂上挂着一份**清单**（要哪些服务、提供什么、按哪版插件接口写的），
所以「装了哪些、各要什么」在**不执行插件代码**的前提下就是可问的：

```bash
uv run dugentx plugins --available    # 这台机器上装了哪些插件包（不读配置、不装载、不需要 key）
```

仓库里那份能跑的插件包在 `examples/dugentx-plugin-clock/`——一个 `now` 工具、一个 `clock` 服务、
一段提示词；用它的配置是 `examples/plugin-package.yml`，整份文件只多了一行。自己怎么做一个，见
[写一个插件](docs/writing-a-plugin.md) 第 6 节。

### 一个回合怎么走

```
turn/start
  把用户那句话追加进日志            ← 从此它才「存在」
  while True:
    messages = derive_view(session)  ← 上下文只能来自日志
    messages = agent/pre-step  (waterfall)   ← 压缩、注入、拒答挂这里
    verify_projection(messages, session)     ← 模型可见 ⟺ 已记录
    request  = agent/request   (waterfall)   ← 改请求的最后一道
    stream   = llm/stream      (waterfall)   ← 包住整条模型流
    记下 assistant/message
    if 回复里没有工具请求: break              ← 这才是退出条件
    for call in 工具请求:
      outcome = tools.execute(call)          ← 四段管道，权限在这里
      记下 tool/result                       ← 结果回填，下一轮模型看得到
turn/end
```

词汇照 dsh：**step** 是一次模型请求加上它请求的工具；**turn** 是零个或多个 step，从拿到一条输入开始，到不再欠模型任何东西为止。

三个刻意的选择：

**退出条件是「这一轮没再请求工具」，不是「跑了 N 轮」。** 按轮数停会腰斩正常任务——一个要读五个文件的任务，在第 8 步时只是干到一半。`max_steps` 是熔断，不是完成判断。

**每一步都重新从日志投影上下文**，而不是在内存里维护一个 messages 列表。看起来慢，但它让「模型看到的」和「日志里记下的」不可能分叉——压缩、恢复会话、子 Agent、审计全都免费得到。

**异常不往上抛**，而是变成 `agent/error` 事件和一次失败的 step。harness 崩掉比模型答错严重得多。

### 工具执行管道

```
tool/call
  tools/pre-execute   (waterfall)   ← 权限、白名单、参数校验、审计
    └─ 工具本身                      ← 不是事件，是 pre-execute 的最内层处理器
  tools/post-execute  (waterfall)   ← 脱敏、截断
tool/result
```

权限（`permissions` 缝）、沙箱、审计、结果裁剪全都挂在 `tools/pre-execute` 上——**没有一个工具需要知道这些政策存在**。

### 五种派发方式

| 方式 | 是否 await | 有返回值 | 用来做什么 |
|---|---|---|---|
| `emit` | 否 | 否 | 观察：日志、计数 |
| `waterfall` | 否 | 是 | 中间件：策略、改写、短路 |
| `parallel` | 是 | 否 | 互不相干的收尾工作 |
| `serial` | 是 | 是 | 依次询问 |
| `bail` | 否 | 是 | 第一个给出答案的胜出 |

**`waterfall` 是重点**：它实现环绕中间件。监听器拿到 `(payload, next)`，调 `next()` 把（可能被改写过的）参数交给下游；不调 `next()` 直接返回就是短路。

派发方式是**公共契约**，所以它被写在 `dugentx/events.py` 里，并由事件总线强制：

```python
bus.emit("tools/pre-execute")     # 报错：这个事件声明的是 waterfall
bus.emit("tool/cals")             # 报错：事件名不在目录里
```

写错事件名是静默失败——监听器永远不触发，没有任何提示。所以这里宁可吵。

### 运行期动态挂载

这是最「动态」的一块，也是 `dugentx/plugins/self_extension.py` 存在的理由。

agent 有一个 `manage_plugin` 工具，可以在会话中途挂载或拔掉自己的插件：

```
manage_plugin(action="list")                                    # 我现在有什么
manage_plugin(action="mount", module="dugentx.plugins.skill")   # 给自己装一个
manage_plugin(action="unmount", plugin_id="skill")              # 拔掉
```

它有三个前提，缺一个都做不干净：

1. **服务共享**：新挂上来的插件 `ctx.provide(...)` 之后，agent 下一次 `visible_tools()` 就多出那个工具，不用重启、不用重载。
2. **装载返回 disposer**：拔掉时，插件注册过的一切一起消失。做不到这一点，动态挂载就只是「装得上，拔不掉」，跑几个回合以后进程里全是幽灵。`tests/test_kernel.py::test_a_plugin_that_bypasses_ctx_leaves_ghosts` 就是这条的反面教材。
3. **挂载本身进日志**：`plugin/mounted` / `plugin/unmounted` 是会话事件，否则恢复会话之后「它当时为什么多了这个工具」就成了无头案。

`manage_plugin` 的标签是 `dangerous`，默认策略会拦下它。这不是保守：**能改自己工具箱的动作，必须在代码侧有人点头**。要放开就在配置里改那一行——但那个决定写得出来、看得见、可审计。

**一个诚实的限制。** 提示词里的**工具清单**是每次装配时现读注册表的，所以挂上来的工具**立刻**出现在模型眼前；但**技能目录**（`skills` 那一段）是在装载时缓存下来的——因为技能目录的来源是异步的，而提示词段落的 `render` 是同步的。所以会话中途挂上技能，技能**工具**当场可用，而提示词里那份目录清单要到下次启动才更新。

这不是没注意到，是一个取舍：为了让 `render` 保持同步，就得有人缓存。真要修，正确的做法是让 skill 插件在目录变化时主动刷新那份缓存，而不是把 `assemble()` 变成 async——后者会把异步传染给每一个段落，这个价钱不值。

## 编码 TUI：它是长出来的，不是装上去的

```bash
uv run python examples/tui_demo.py     # 不需要 key，看一遍完整画面
uv run dugentx tui                     # 真跑
```

它长这样：

```
DugentX
  model=deepseek-flash  session=b0d7694f  tools=10
  cwd=...\DugentX
• 把 demo-note.txt 里的状态改成完成
── step 0
▸ 我先读一下这个文件。

╭─ read_file  examples/workspace/demo-note.txt
· read_file(path='examples/workspace/demo-note.txt')（只读，直接放行）
│ ✓ read_file
│   examples/workspace/demo-note.txt（共 2 行，显示 1-2）
│   1	# 演示用的一行笔记
│   2	状态：待办
── step 1
▸ 读到了，是一行待办。我把它改成完成状态。

╭─ edit_file  examples/workspace/demo-note.txt  old=状态：待办  new=状态：完成
同意：edit_file(path='examples/workspace/demo-note.txt', old='状态：待办', new='状态：完成')
│ ✓ edit_file
│   已在 examples/workspace/demo-note.txt 里替换 1 处
--- examples/workspace/demo-note.txt
+++ examples/workspace/demo-note.txt
@@ -1,2 +1,2 @@
 # 演示用的一行笔记
-状态：待办
+状态：完成
── step 2
▸ 改完了。diff 在上面——你看到的就是这个 TUI 存在的理由。

── turn completed  steps=3  tools=2  tokens=0
```

### 它为什么能「原样长出来」

因为**「跟人说话」这件事本来就不该由审批逻辑决定**。

原来的写法是审批自己去读 stdin。一旦界面换成 TUI，这条线就断了：TUI 有自己的输入行、自己的键位、自己的重绘节奏，它不可能让审批去调 `input()`。

所以第 12 条缝就是这件事本身：

```
permissions 缝  ──依赖──▶  human 缝  ◀──实现──  stdio 通道 / TUI 通道
（决定要不要问）           （只负责问）          （怎么问是它的自由）
```

于是「加一个编码 TUI」的全部改动是这个：

```yaml
# dugentx.tui.yml —— 整份文件就这些
extends: dugentx.yml
patch:
  - id: human
    disabled: true          # 不再用 stdio 通道
  - id: tui
    plugin: dugentx.plugins.tui
```

**循环没改、工具管道没改、审批一行没改。** `tests/test_tui_app.py` 里有几条测试专门盯着这件事：它从真实的 `dugentx.tui.yml` 读配置、`AgentRuntime.boot()` 起来，断言换掉的只有 `ctx.human` 的实现，其余服务一个不少。

### 包里的四件东西，以及为什么是四件

| 文件 | 管什么 |
|---|---|
| `terminal.py` | 终端本身：按平台切原始模式、解析按键、ANSI 与配色 |
| `render.py` | 把一次事件画成几行字（流式正文、工具卡、diff、状态行） |
| `paint.py` | 订阅事件，翻译成 `render` 的调用 |
| `app.py` | 主循环：读一行、跑一个回合、再来一次 |

后两个分开，是因为**显示和输入本来就是两件事**：没有输入循环的时候（跑脚本、跑测试），画面照样应该更新。

`paint.py` 里有一个决定值得单说：**diff 不是从工具输出里解析出来的。** 工具的输出是给模型看的，它随时可能改写法；而且在执行前读一遍文件、执行后再读一遍，拿到的永远是事实。所以画师在 `tools/pre-execute` 上拍快照，在 `tools/post-execute` 上对比——两个都是 waterfall，都会被 await，顺序也就确定了。

### 一个界面如果只能靠手敲才算测过，那它等于没测

`terminal.py` 的输入侧是一个可注入的 `KeyReader`：真终端用 `StdioKeyReader`（Windows `msvcrt` / POSIX `termios`，含东亚宽字符与代理对），测试用 `ScriptedKeyReader`。输出侧是一个可注入的 `Screen`。

所以 `tests/test_tui_app.py` 能这样写，而且不需要终端：

```python
keys = ScriptedKeyReader(["你好", "[enter]", "[eof]"])
local = TuiApp(ctx, screen=Screen(StringIO(), color=False), keys=keys, renderer=Renderer(screen))
await local.run()
```

含中文输入、Ctrl-W 整词删除、方向键翻历史、宽字符按列计算光标——这些都离线跑。

## 无 key 也能跑一遍完整流程

```bash
uv run dugentx -c dugentx.replay.yml run "读一下 hello.txt，然后教我怎么写提交信息"
```

那五个 step 里只有模型是假的：

```
· step 1   read_file            ← 真读了一个真文件
· step 2   manage_plugin        ← 会话中途挂上技能**提供者**（要过权限）
· step 3   manage_plugin        ← 再挂上需要它的**工具包**（use_skill 此刻才出现）
· step 4   use_skill            ← 用刚出现的那个工具
· step 5                        ← 收尾
```

挂完之后 `dugentx replay <id>` 能看见两件平时看不见的事：`plugin/mounted` 事件，
以及一条 `reason=change` 的 `system/message`——那正是工具清单变长的那一刻被记下来的提示词。

---

## 三条设计原则

**一、模型可见 ⟺ 已记录。**

发给模型的每一条消息，都必须能从会话日志重建。`verify_projection()` 在每个 step 之前跑一次。

这条规则一次性解决三件事：持久化就是把日志写下来；恢复会话就是读日志重新投影；上下文压缩就是在投影这一步做的操作，日志不动，所以压缩可审计、可回退。

压缩怎么还能成立？它不改日志，而是追加一条 `context/compacted` 事件，记录「丢到哪个 seq、摘要是什么」。`derive_view()` 据此重建模型看到的东西。多次压缩只认最后一条，因为后一次摘要已经包含前一次。

**二、注册即可撤销。**

每一笔注册都走 `ctx.provide` / `ctx.on` / `ctx.effect`，卸载就是把插件的子作用域整个撤销。插件不需要写 `uninstall`。

**三、插件，而不是改循环。**

新行为挂在有文档的扩展点上。改变一件产品的行为，应该是加一个插件，而不是到一个三千行的循环里插一个 `if`。`dugentx.yml` 那个「哪里该写什么」的对照表就是这条原则的落地。

---

## 目录

```
dugentx/
  kernel/        可拔插本身：context / plugin / effect / events / loader
  seams/         12 个能力缝的服务定义（只有接口与词汇）
  providers/     实现：any-llm 适配器、回放适配器、本地 fs / shell、人机通道、压缩、子 Agent、技能
  tools/         模型可调用的工具（fs / shell / skill / subagent / ask_human）
  plugins/       把服务与工具接起来的装载单元
  tui/           编码 TUI：terminal / render / paint / app
  events.py      事件目录
  runtime.py     开机：组合 → 插件树 → agent
  cli.py         命令行
examples/
  skills/        两个真能用的技能示例
  tui_demo.py    无 key 看一遍 TUI
  workspace/     演示用的工作区
tests/           全部离线，不需要 key
dugentx.yml          默认组合（DeepSeek + stdio 人机通道）
dugentx.tui.yml      编码 TUI：extends 上面那份，只改一行
dugentx.replay.yml   无 key 演示组合（回放适配器）
```

配置支持 `extends`：基座的插件行先来，自己的追加在后，最后统一应用 `patch`。
**「换一个人机通道」这件事只值两行 patch**，不需要复制整份配置——
复制出来的两份会各自漂移，那是配置最容易烂掉的方式。

---

## 和 dsh 的关系

架构参考 [dsh（DeepSeek Harness）](https://github.com/deepseek-ai/deepseek-harness) 的可拔插模型，用 Python 重写成一个能读完的体量。**照搬的**：

- 上下文即服务仓库，插件靠键拿服务，从不 import 实现
- 能力缝的三个角色划分
- 五种事件派发方式，waterfall 作为环绕中间件
- 「注册是 effect，装载与卸载对称」
- 「模型可见 ⟺ 已记录」
- turn / step 的词汇
- 「新行为去扩展点，不要改循环」

**刻意简化的，都写在这里以免误解**：

| dsh | DugentX | 为什么 |
|---|---|---|
| Cordis 的 `inject` 是响应式的，服务晚到会等待唤醒 | 装载前拓扑排序，缺服务**在装载时大声失败** | 少一个响应式调度器，换来一条更好查的规则：跑到一半才发现缺服务，说明配置写错了 |
| profile → bundle → patch 三层叠加 | `extends` + 一份 `plugins` 列表 + 按 id 的 `patch` | 两层已经能表达「换 adapter」「关一个工具」「换一个人机通道」；再多的层，配置会先于代码变得难懂 |
| 几十个包，每个缝一个可安装包 | 一个包，按缝分模块 | 让新人一个下午能读完 |
| 会话格式有版本与迁移链 | JSONL，一行一个事件 | 迁移机制的价值在跨版本兼容，这里没有历史包袱要背 |

**没有简化掉的**：运行期挂载/卸载、watermark 式的事件契约校验、可拔插的循环本身（`ctx.agentLoop` 换成别的东西，就是另一个产品）。

---

## 新人从哪读起

建议的顺序，每步都能跑：

1. `dugentx/kernel/context.py` + `effect.py` —— 先理解「服务仓库 + 可撤销注册」。**这两页是全部。**
2. `dugentx/kernel/events.py` 的 `waterfall` —— 理解中间件怎么写。然后跑 `dugentx events` 看真实的目录。
3. `dugentx/providers/agent_loop_basic.py` —— 循环本体，一行一行对照上面那张 turn/step 图。
4. `dugentx/seams/tools.py` —— 四段管道。然后看 `dugentx/seams/permissions.py` 怎么用 `tools/pre-execute` 把权限插进去，一行都不用改工具。
5. `dugentx/plugins/self_extension.py` —— 它把前面所有东西串起来：为什么注册必须可撤销、为什么事件名要校验、为什么挂载要进日志。
6. `dugentx/seams/human.py` + `dugentx/plugins/tui.py` —— 最后读这两个。它们回答的是「为什么加一个界面没有动 harness」：因为「跟人说话」本来就是一条缝，而不是审批逻辑里的一个 `input()`。

想动手的话，这几个练习都不大，而且每一个都强制你去用它对应的那条缝：

- 加一个 `web_fetch` 工具（打 `network` 标签，确认会自己从 `tools/pre-execute` 上长出来）
- 把 `ctx.agentLoop` 换成并行工具调用的版本——只换一个服务，别的都不动
- 写一个新的 `Compactor`，用检索而不是截断
- 写一个新的 `HumanChannel`：比如把所有提问转发到一个 webhook，让审批在手机上点。**`ctx.human` 是唯一要动的东西。**
- 把自己的插件做成一个**包**：一条 entry point 加一份清单，然后 `dugentx plugins --available` 就该列出它。模板是 `examples/dugentx-plugin-clock/`，配置侧只写包名。
- 给 TUI 加一个「思考链」开关：`Delta.reasoning` 已经在流里了，`paint.py` 现在故意丢掉它

---

## 开发

```bash
uv sync --all-extras                 # 装齐 dev + tui 两组可选依赖
uv run pytest -q                     # 302 passed，全程离线，不需要 key
uv run ruff check .                  # 静态检查
uv run python scripts/check_boundaries.py   # 把两条硬规则变成断言
uv run python scripts/gen_docs.py --check   # docs/events.md 有没有落后于事件目录
```

> [!warning] `uv sync --extra tui` 会顺手把 dev 卸掉
> 可选依赖是**按次声明**的：只写 `--extra tui` 就等于「只要这一组」，
> `pytest` / `ruff` 会被移除。而 `uv run pytest` 在这之后**不会报「没装 pytest」**——
> 它会去 PATH 上找一个系统的 pytest，然后因为那个环境里没有 dugentx 而报
> `ModuleNotFoundError: No module named 'dugentx'`。错误信息指向你的代码，问题却在依赖组。
>
> 要么写 `uv sync --all-extras`，要么把两组都列上：`uv sync --extra dev --extra tui`。

### 边界可以被检查，而不是只能被相信

`scripts/check_boundaries.py` 做三条静态断言：

1. 只有 `providers/llm_anyllm.py` import 了 `any_llm`——别的地方想调模型必须经过 `ctx.llm`；
2. 任何地方都不许 import `requests` / `httpx` / `aiohttp` / `urllib.request` / `socket`；
3. `dugentx/kernel/` 不许反向 import `seams` / `providers` / `tools` / `plugins`。

规则写在 README 里只是意愿，能被脚本检查才是约束。

`tests/` 里的测试全部不需要 API key：模型层用 `providers/llm_replay.py` 的回放适配器，文件系统与命令执行在 `tmp_path` 上跑。**这是刻意的**——一个需要 key 才能跑的 harness，等于把大部分想读它的人挡在门外。
