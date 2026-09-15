<!-- 由 scripts/gen_docs.py 生成，不要手改。 -->
<!-- 改事件请改 dugentx/events.py，然后 `uv run python scripts/gen_docs.py`。 -->

# 事件目录

内核支持 5 种派发方式，当前目录用到 2 种。区别在于**你能不能拦下它**：

| 方式 | 你能做什么 | 说明 |
|---|---|---|
| `emit` | 只能看 | 通知。没有返回值，别指望它影响流程。适合日志、指标、界面。 |
| `waterfall` | 可以拦下来 | 环绕中间件。改写参数、拒绝、或者什么都不做放过去；**必须调 `nxt()`**，否则后面的处理器（包括工具本身）不会执行。权限拦截、上下文注入、请求改写全挂在这上面。 |

当前目录没有用到的：`bail`、`parallel`、`serial`。内核实现了它们，只是还没有事件声明用上。

`EventBus` 会**在派发时校验**：发一个声明为 waterfall 的事件用 `emit`，当场报错；发一个目录里没有的名字，也当场报错。写错事件名本来是静默失败——监听器永远不触发，没有异常，只有「怎么没生效」。

## `waterfall`：可以拦下来

环绕中间件。改写参数、拒绝、或者什么都不做放过去；**必须调 `nxt()`**，否则后面的处理器（包括工具本身）不会执行。权限拦截、上下文注入、请求改写全挂在这上面。

| 事件 | 载荷 | 含义 | 谁发 | 谁在听 |
|---|---|---|---|---|
| `agent/pre-step` | `messages` | 决定这一步收哪些输入；可以改写，也可以拒绝 | `dugentx/providers/agent_loop_basic.py` | `dugentx/plugins/compaction.py` |
| `agent/request` | `request` | 模型请求发出前的最后一道；改写请求就挂这里 | `dugentx/providers/agent_loop_basic.py` | — |
| `llm/stream` | `request`, `stream` | 包住整条模型流 | `dugentx/providers/agent_loop_basic.py` | — |
| `tools/pre-execute` | `call` | 工具执行前的策略层：权限、白名单、参数校验、审计 | `dugentx/seams/tools.py` | `dugentx/seams/permissions.py`<br>`dugentx/tui/paint.py` |
| `tools/post-execute` | `outcome` | 工具结果后处理：脱敏、截断、转成模型看得懂的形式 | `dugentx/seams/tools.py` | `dugentx/tui/paint.py` |

## `emit`：只能看

通知。没有返回值，别指望它影响流程。适合日志、指标、界面。

| 事件 | 载荷 | 含义 | 谁发 | 谁在听 |
|---|---|---|---|---|
| `turn/start` | `prompt` | 一个回合开始 | `dugentx/providers/agent_loop_basic.py` | `dugentx/tui/paint.py` |
| `turn/end` | `result` | 一个回合结束 | `dugentx/providers/agent_loop_basic.py` | `dugentx/tui/paint.py` |
| `step/start` | `index` | 一次模型请求开始 | `dugentx/providers/agent_loop_basic.py` | `dugentx/tui/paint.py` |
| `step/end` | `index`, `tool_calls` | 一次模型请求结束 | `dugentx/providers/agent_loop_basic.py` | `dugentx/tui/paint.py` |
| `llm/chunk` | `delta` | 收到一个流式增量 | `dugentx/providers/agent_loop_basic.py` | `dugentx/tui/paint.py` |
| `llm/usage` | `usage` | 一次调用的开销 | `dugentx/providers/agent_loop_basic.py`<br>`dugentx/providers/compaction_basic.py` | `dugentx/tui/paint.py` |
| `model/switched` | `provider`, `model` | 运行期换了 provider 或模型 | `dugentx/providers/model_switch.py` | `dugentx/tui/paint.py` |
| `tool/call` | `call` | 模型请求了一次工具调用 | `dugentx/seams/tools.py` | `dugentx/tui/paint.py` |
| `tool/result` | `outcome` | 工具结果已回填 | `dugentx/seams/tools.py` | — |
| `permission/skip` | `request` | 这一档不需要问，直接过 | `dugentx/seams/permissions.py` | `dugentx/tui/paint.py` |
| `permission/decided` | `request`, `decision` | 人给了答复 | `dugentx/seams/permissions.py` | `dugentx/tui/paint.py` |
| `context/compacting` | `before` | 开始压缩上下文 | `dugentx/plugins/compaction.py` | — |
| `context/compacted` | `result` | 压缩完成 | `dugentx/plugins/compaction.py` | — |
| `plugin/mounted` | `plugin`, `row` | 运行期挂载了一个插件 | `dugentx/plugins/self_extension.py` | `dugentx/tui/paint.py` |
| `plugin/unmounted` | `plugin` | 运行期拔掉了一个插件 | `dugentx/plugins/self_extension.py` | `dugentx/tui/paint.py` |
| `skill/catalog` | `count` | 技能目录被读取 | `dugentx/providers/skill_filesystem.py` | — |
| `skill/loaded` | `name`, `tokens` | 一个技能被拉进上下文 | `dugentx/tools/skill_tools.py` | `dugentx/tui/paint.py` |
| `human/asked` | `question`, `source` | 有人被问了一句 | `dugentx/tools/ask_tools.py` | — |
| `human/answered` | `question`, `answer` | 人回答了 | `dugentx/tools/ask_tools.py` | — |
| `subagent/start` | `request` | 子 Agent 出发 | `dugentx/providers/subagent_inprocess.py` | — |
| `subagent/end` | `result` | 子 Agent 回来 | `dugentx/providers/subagent_inprocess.py` | — |
| `agent/error` | `where`, `error` | 循环里出了错，但没有崩 | `dugentx/providers/agent_loop_basic.py`<br>`dugentx/providers/compaction_basic.py`<br>`dugentx/providers/subagent_inprocess.py` | `dugentx/tui/paint.py` |

## 没人听的事件

下面这些有声明、有人发，但**当前代码里没有任何监听者**。这不一定是错的——它们是对外开放的扩展点，只是还没有插件用。但如果加完之后这里一直是一长串，值得问一句是不是该删。

- `agent/request`
- `llm/stream`
- `tool/result`
- `context/compacting`
- `context/compacted`
- `skill/catalog`
- `human/asked`
- `human/answered`
- `subagent/start`
- `subagent/end`

## 没人发的事件

没有。目录里每个事件都至少有一处派发。

## 事件名有两个命名空间

`session/start`、`session/end` 这类名字**只存在于会话日志**里（`dugentx/seams/session.py` 的 `EventKind`），不在上面这份总线目录里。两者同名不等于同一个东西：

- **日志种类**是「发生过什么」的记录，只能读，用来投影出模型看到的历史。
- **总线事件**是「此刻可以拦下或反应什么」，只能订阅，用来扩展行为。

`turn/start` 这类两边都有：边界既值得记进日志，也可能有人要当场反应。而 `session/start` 只在日志里——一个会话开始了是事实，没有谁需要拦下它。

## 这张表的边界

「谁发 / 谁在听」两列是**静态扫描**得到的：认字符串字面量、模块级常量、以及 `on = ctx.on` 这种别名写法。运行期算出来的事件名认不出来，而运行期挂载的插件也不在扫描范围里（比如 `self-extension` 能在跑起来之后再加监听器）。所以这两列是**下界**，不是全部。
