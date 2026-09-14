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

# 无 key 也能跑：回放适配器，不联网
uv run dugentx -c dugentx.replay.yml run "读一下 hello.txt 然后告诉我里面写了什么"

# 真跑（需要一个 provider 的 key，只从环境变量读）
export MISTRAL_API_KEY=...
uv run dugentx run "看看这个目录里有什么，然后写一份 README 摘要"
```

几条用来「看」的命令：

```bash
uv run dugentx plugins                  # 这次到底装了哪些插件，按什么顺序
uv run dugentx events                   # 事件目录：哪些是通知，哪些是中间件
uv run dugentx sessions                 # 本地会话日志
uv run dugentx replay <session_id>      # 把一个历史会话重新投影成模型看到的样子
uv run pytest -q                        # 全部测试离线跑，不需要 key
```

`replay` 这条命令值得单独试一次。它把「模型可见 ⟺ 已记录」从一句口号变成你随时能跑一下、看一眼的东西。

---

## 架构

### 内核只有五件东西

`dugentx/kernel/` 里那五个模块构成了「可拔插」本身，它们不知道 Agent 是什么：

| 模块 | 干什么 |
|---|---|
| `context.py` | 服务仓库。插件靠 `ctx.<key>` 拿服务，**从不 import 具体实现** |
| `plugin.py` | 插件 = 名字 + 要用的服务（`inject`）+ 一段 `apply` |
| `effect.py` | 可撤销的副作用。每笔注册返回一个 disposer |
| `events.py` | 事件总线，五种派发方式 |
| `loader.py` | 读配置 → 校验 → 拓扑排序 → 实例化 |

### 能力缝：三个角色，缺一不可

一个**完整的缝**有三个角色，这是 dsh 的划分方式，DugentX 照搬：

| 角色 | 是什么 | 在哪 |
|---|---|---|
| Service Definition | 接口与数据词汇 | `dugentx/seams/` |
| Service Provider | 具体实现 | `dugentx/providers/` |
| Consumer | 用它的东西，通常是模型可调用的工具 | `dugentx/tools/` |

只有接口没有实现是设计稿；只有实现没有消费者是死代码。

**11 个缝：**

| 缝 | 服务键 | 一句话 |
|---|---|---|
| `llm` | `ctx.llm` | 模型适配器注册表；唯一允许依赖 any-llm 的地方 |
| `tools` | `ctx.tools` | 工具注册表 + 四段执行管道 |
| `agent` | `ctx.agents` / `ctx.agent` | 会话载体、回合与步的词汇 |
| `session` | `ctx.session` | 只能追加的事件日志，以及从它投影出的模型历史 |
| `prompt` | `ctx.prompt` | 系统提示词按段注册、按序拼装 |
| `fs` | `ctx.fs` | 文件访问 + 路径策略 |
| `shell` | `ctx.shell` | 命令执行，带超时与熔断 |
| `permissions` | `ctx.permissions` / `ctx.approval` | 三档权限，开关在代码侧 |
| `compaction` | `ctx.compaction` | 上下文预算满了怎么办 |
| `subagent` | `ctx.subagents` | 把一件事整个交出去 |
| `skill` | `ctx.skills` | 按需加载的说明文档 |

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
    tools/execute     (waterfall)   ← terminal 是工具自己的处理器
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
  seams/         11 个能力缝的服务定义（只有接口与词汇）
  providers/     实现：any-llm 适配器、回放适配器、本地 fs / shell、压缩、子 Agent、技能
  tools/         模型可调用的工具（fs / shell / skill / subagent）
  plugins/       把服务与工具接起来的装载单元
  events.py      事件目录
  runtime.py     开机：组合 → 插件树 → agent
  cli.py         命令行
examples/
  skills/        两个真能用的技能示例
tests/           全部离线，不需要 key
dugentx.yml      真实组合（any-llm）
dugentx.replay.yml  无 key 演示组合（回放）
```

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
| profile → bundle → patch 三层叠加 | 一份 `plugins` 列表 + 按 id 的 `patch` | 示例体量下，一层已经能表达「换 adapter」「关一个工具」「改一行配置」这三件真会发生的事 |
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
5. `dugentx/plugins/self_extension.py` —— 最后再读这个。它把前面所有东西串起来：为什么注册必须可撤销、为什么事件名要校验、为什么挂载要进日志。

想动手的话，`README` 里没有列、但很容易加的三个练习：

- 加一个 `web_fetch` 工具（`network` 标签，走 `tools/pre-execute` 的确认）
- 把 `ctx.agentLoop` 换成并行工具调用的版本
- 写一个新的 `Compactor`，用检索而不是截断

---

## 开发

```bash
uv run pytest -q                    # 全部离线
uv run ruff check .                 # 静态检查
uv run python scripts/check_boundaries.py   # 把两条硬规则变成断言
```

### 边界可以被检查，而不是只能被相信

`scripts/check_boundaries.py` 做三条静态断言：

1. 只有 `providers/llm_anyllm.py` import 了 `any_llm`——别的地方想调模型必须经过 `ctx.llm`；
2. 任何地方都不许 import `requests` / `httpx` / `aiohttp` / `urllib.request` / `socket`；
3. `dugentx/kernel/` 不许反向 import `seams` / `providers` / `tools` / `plugins`。

规则写在 README 里只是意愿，能被脚本检查才是约束。

`tests/` 里的测试全部不需要 API key：模型层用 `providers/llm_replay.py` 的回放适配器，文件系统与命令执行在 `tmp_path` 上跑。**这是刻意的**——一个需要 key 才能跑的 harness，等于把大部分想读它的人挡在门外。
