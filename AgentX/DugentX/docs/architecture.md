# 架构：为什么 DugentX 长成这样

这份文档讲的是**为什么**。接口清单在 `docs/seams.md`，事件目录在 `docs/events.md`，怎么跑起来在 `README.md`。

## 一句话的想法

**DugentX 是一个插件组合，不是一个框架。**

整套设计只有一条机制：一个服务仓库（`Context`），加上一组**可以撤销的注册**（`EffectScope`）。插件通过服务键拿到能力，从不 import 具体实现；配置决定装哪些插件；每个插件声明自己要哪些键（`inject`），装载器据此排出顺序。

于是「换掉一个东西」在同一套机制下有三种形态，改的都是配置：

| 想做的事 | 动作 | 例子 |
|---|---|---|
| 换实现 | 把那一行的 `plugin` 指向别的模块 | `dugentx.plugins.human` → `dugentx.plugins.tui` |
| 换参数 | 改那一行的 `config` | `model` / `provider` / `api_key_env` |
| 关掉能力 | 给那一行 `disabled: true` | 不装文件工具 |

调用方一行都不改。这条性质不靠自律维持：`scripts/check_boundaries.py` 会检查「有没有人绕过缝自己去调模型」。

## 内核：七个文件，与 Agent 无关

`dugentx/kernel/` 里现在有七件东西。它们不知道 Agent 是什么，也不 import 任何能力实现（这一条由边界检查强制）：

| 文件 | 角色 |
|---|---|
| `context.py` | 服务仓库，以及三个可撤销的注册入口 |
| `plugin.py` | 插件 = 名字 + `inject` + `provides` + 一段 `apply`，以及挂在工厂上的那份清单 |
| `manifest.py` | 清单：一个插件包对外的「一张脸」（`PluginManifest`、`manifest_of`） |
| `registry.py` | meta 接口：已安装插件包的名单，以及 `plugin:` 的解析规则 |
| `effect.py` | effect 树，`dispose()` 按注册的相反顺序撤销 |
| `events.py` | 事件总线，五种派发方式 |
| `loader.py` | 读 YAML → 覆盖 → 校验 → 拓扑排序 → 实例化 |

（`errors.py` 是一小棵异常树，`__init__.py` 只做出口。这两个不算「一件东西」，它们不含机制。）

### 后两个文件是 meta 层：检查，但不执行

前五个回答的是「一个插件装上去之后会发生什么」。`manifest.py` 与 `registry.py` 回答的是另一类问题：
**装了哪些插件、各自要什么、给什么、按哪一版接口写的**——而回答这些**不需要执行插件的代码**。

这不是顺手加的元数据。在此之前，一个插件如果只能是配置里的一串 `包.模块:属性`，那它在被装载之前
什么都问不出来：它要哪些服务？提供什么？对不对得上这个版本？唯一的办法是把模块 import 进来、
把工厂调起来——也就是执行它。于是「先看看装了些什么、再决定装哪些」变成一件做不到的事，
`dugentx plugins --available` 这种命令也就不可能存在（它既没有配置、也没有 key）。

清单（`manifest.py`）解决的就是这件事：它是**声明**，不是执行结果。`define_plugin` 把它挂在
**工厂函数**上（`__dugentx_manifest__`），不是在工厂里返回，所以读清单不会调用工厂。清单里的
`inject` / `provides` 与 `Plugin` 上那两个字段同源，因此不会分叉。

注册表（`registry.py`）是 DugentX 的 **meta 接口**：「meta」是相对于「装载」说的——装载是执行一个
插件，注册表只回答关于插件的问题。它通过标准 entry point（组名 `dugentx.plugins`）发现插件包，
只 `import` 插件那个模块、不调用工厂。于是：

- `dugentx plugins --available` 不需要 key、不需要把组合装起来，就能列出这台机器上有哪些插件包；
- 装载器在动手之前就能判断「这个插件要的服务没人提供」；
- 接口版本对不上时，报错说的是**版本**（`插件包 'x' 声明的是插件接口 v99，本机这个 harness 是 v1`），
  而不是插件里某一行 `AttributeError`。

配置里照旧可以写 `包.模块:属性` 指向仓库内的模块——那条路一点没变，仓库自己那十几个插件走的就是它。
两种写法的判据是**注册表里有没有这个名字**，不是字符串里有没有点：`module:attr` 里的 module
完全可以是个不带点的顶层模块名（`my_module:create` 这种写法到处都是）。所以一个已安装的包名
哪怕长成 `dugentx.plugins.shell`，赢的也是注册表里那一条。`loader.py` 的 `resolve_factory` 现在只是
`registry.py` 那一个的再导出，`from dugentx.kernel.loader import resolve_factory` 这类老写法照旧能用。

注册表的来源是**可注入**的（`PluginRegistry(source=...)`），所以发现、解析、版本校验这三条路
都能在不安装任何东西的前提下离线测——`tests/test_plugin_registry.py` 就是这么做的。

### `Context` 给了你什么

```python
def provide(self, key: str, instance: Any) -> Disposer
def get(self, key: str, default: Any = None) -> Any
def has(self, *keys: str) -> bool
def service(self, key: str) -> Any                     # 没有就抛 ServiceNotFound
def services(self) -> dict[str, Any]
def require(self, *keys: str) -> None
def effect(self, fn: Callable[[Context], Disposer | None], *, label: str = "") -> Disposer
def on(self, event: str, listener: Listener, *, prepend: bool = False, once: bool = False) -> Disposer
def child(self, name: str) -> Context
async def mount(self, plugin: Plugin) -> Disposer
def dispose(self) -> None
```

几个值得单独说的点：

- `ctx.tools` 这种写法来自 `__getattr__`，它只对**已注册的服务键**生效，其他属性照常抛 `AttributeError`。所以 `hasattr` 仍然可信，拼错的键不会静默变成 `None`。
- `service(key)` 拿不到就抛 `ServiceNotFound` 并列出当前可用的键，**不回退到默认实现**。回退会让「我明明配了那个插件，怎么没生效」变成一个查不出来的问题。
- `provide(key, instance)` 遇到已经存在的键会抛 `PluginError`，并报出占用者是谁。**同一个键只能有一个提供者**——这正是「换掉实现」的方式：改配置，不是叠加。
- `mount` 在装载前检查 `plugin.inject` 里的每个键是否已经在仓库里，缺任何一个就抛 `PluginError` 并把可用的键列出来。

### 为什么 disposal 是核心原语

`EffectScope.add(disposer, *, label="")` 挂上一笔撤销，返回一个「只撤销这一笔」的句柄。`dispose()` 后进先出地跑完所有 disposer，而且**一笔失败不阻止剩下的被撤销**：失败的记下来，最后汇总抛一个 `DuGentXError`。作用域可以嵌套（`EffectScope.child(name)`），父作用域关闭时子作用域一起关闭。

`Disposer` 只是 `Callable[[], None]`。看起来微不足道，但它是「可拔插」和「启动时跑一段代码」之间的全部差别：没有它，一个被拔下来的插件会把自己的服务、监听器、工具留在仓库里，变成幽灵。

## 注册是 effect：三个入口，一条不变量

`Context` 上真正能改变状态的方法只有三个，它们都返回 `Disposer`：

- `provide(key, instance)` —— 往仓库里放一个服务；
- `on(event, listener, ...)` —— 往总线上挂一个监听器；
- `effect(fn, label=...)` —— 跑一段你自己的注册代码，并告诉作用域怎么撤销它（`fn` 可以返回自己的 `Disposer`，不返回就记一个空操作）。

**不变量：任何东西都不许只通过这三者之外的方式注册，因为只有这三者能撤销。**

这条不变量是运行期动态挂载能成立的全部理由。`mount` 做的事情恰好对称：装载 = 建一个子作用域（`Context.child`，服务共享、注册独立），卸载 = 把那个作用域整个 `dispose()`。所以插件不需要写 `uninstall`——它只需要保证每一笔注册都走了上面三条路。

反面的写法在测试里摆着：`tests/test_kernel.py::test_a_plugin_that_bypasses_ctx_leaves_ghosts`。那个插件调了 `c.service("tools").register("left_behind")`，却没有把返回的 disposer 交回作用域（`ctx.effect`），所以拔掉它之后那个工具还在表里——一个幽灵。

## 事件总线

`EventBus` 按名字分发，每个注册返回一个 `Disposer`。

### 五种派发方式

`dugentx/kernel/events.py` 实现了五种。**派发方式是公共契约的一部分**：一个事件是「通知」还是「中间件」，调用方必须一眼看出来。

| 方式 | 是否 await | 顺序 | 有返回值 | 用来做什么 |
|---|---|---|---|---|
| `emit` | 否 | 注册顺序 | 否 | 观察：日志、计数、指标 |
| `waterfall` | 否 | 注册顺序，可包裹 | 是 | 中间件：策略、改写、短路 |
| `parallel` | 是 | 并行 | 否 | 互不相干的多件收尾工作 |
| `serial` | 是 | 注册顺序 | 是 | 依次询问，拿到结果 |
| `bail` | 否 | 注册顺序 | 是 | 第一个给出答案的胜出 |

`dugentx/events.py` 那份目录当前只用到两种：`emit` 和 `waterfall`。另外三种内核实现了，只是还没有事件声明用上。这不是死代码的下场，而是「总线先于用例」——它们是留给下一批扩展点的。

### `waterfall` 是环绕中间件

`waterfall(name, *args, terminal=...)` 把监听器按注册顺序**包在** `terminal` 外面：第一个注册的在最外层，所以 `prepend=True` 能抢到最外层位置。监听器的签名是：

```python
async def listener(payload, nxt):
    ...
```

- 调 `nxt()`：继续往下，返回值就是下游的结果；
- 调 `nxt(*new_args)`：换掉载荷再往下——改写请求靠这个；
- 不调 `nxt()` 直接返回：**短路**，下游和 `terminal` 都不会跑。

**每个监听器都必须调 `nxt()`，除非它打算拒绝这次调用。** 这不是风格约定，是管道能成立的前提：

- 权限门（`dugentx/seams/permissions.py` 的 `install_gate`）挂在 `tools/pre-execute` 上，用 `raise PermissionDenied(...)` 拒绝，或者 `return await nxt()` 放行；
- TUI 的画师（`dugentx/tui/paint.py`）也挂在同一个事件上，只为了在执行前给文件拍一张快照。它**没有权力拦下任何调用**，所以它的监听器必须老老实实调 `nxt()`。

第二个例子回答了一个很具体的问题：**一个渲染层为什么不能悄悄吞掉一次工具调用。** 因为真正干活的那个 `terminal`（工具自己的处理器）只在 `nxt()` 链的下游，不调它工具根本不会执行。而这件事不会静默发生：`ToolRegistry.execute` 在瀑布返回之后检查结果类型：

```python
if not isinstance(outcome, ToolOutcome):
    raise ToolError(
        f"tools/pre-execute 返回了 {type(outcome).__name__}，应当是 ToolOutcome"
    )
```

所以「监听器忘了 `nxt()`」的表现是一次响亮的 `ToolError`，而不是模型收到一个空结果、然后在空白上继续推理。

## 事件目录在派发时校验

`dugentx/events.py` 里是一份 `EVENTS: list[EventSpec]`，每个 `EventSpec` 有 `name` / `mode` / `summary` / `payload`。`mode` 的合法值由内核里的 `ALLOWED_MODES` 定义，`EventSpec.__post_init__` 拒绝其他任何值：

```python
ALLOWED_MODES: frozenset[str] = frozenset({"emit", "waterfall", "parallel", "serial", "bail"})

if self.mode not in ALLOWED_MODES:
    raise DuGentXError(f"未知的派发方式：{self.mode}")
```

`AgentRuntime` 启动时把这份目录翻译成 `EVENT_MODES`（`{名字: 方式}`）交给总线：

```python
self.ctx = Context("root", events=EventBus(EVENT_MODES))
```

`EventBus._check` 在**每一次派发时**跑：名字不在目录里，报错；声明的方式和实际用的方式不一致，报错。目录为空时校验整体跳过（`if not self._catalog: return`），所以只有拿着目录建的总线才有这道保护。

要新增一个事件，先写进 `dugentx/events.py`。

### 载荷是 watermark 式的契约

每条 `EventSpec` 用 `payload` 声明载荷的字段名。这份清单就是**调用方与监听器之间的契约**：`emit("turn/start", prompt)` 里的 `prompt`、`step/end` 的 `(index, tool_calls)`，都写在目录里。载荷按位置传参，所以字段顺序也是契约的一部分——目录是唯一能查到它的地方。

但要说清楚校验覆盖到哪：目录拦的是**名字**和**方式**，不拦载荷。拼错的事件名和用错的派发方式本来是静默失败（监听器永远不触发，没有异常，只有「怎么没生效」），所以这里宁可吵。而「第一个参数传了别的对象」这类错误不在目录的射程里，它靠类型标注和测试。

## turn 与 step

词汇照 dsh，**定义**和它一致：

- **step（步）**：一次模型请求，加上它请求的那些工具。这是最小的推进单位。
- **turn（回合）**：零个或多个 step。从「拿到一条用户输入」开始，到「不再欠模型任何东西」为止。

为什么要有 turn 这个概念：一次问答和一次请求不是一回事。模型可能要工具、拿到结果、再要一次，这些都属于同一个回合。

> [!warning] 一个词，两种用法
> dsh 的中文文档把 turn 叫「**轮次**」，本文档叫「回合」——同一个东西，只是译法不同。真正要小心的是**另一种**用法：有些工具和文章（比如 pi）把「一次模型请求 + 它请求的工具」也叫 turn，那其实是这里的 **step**。
>
> 混掉的代价很具体：说「它跑了 3 轮」，对方分不清是用户问了三次，还是用户只问了一次、模型伸手要了三次工具。而在日志、账单和 `max_steps` 里，这是两件事。看到别人写 turn 的时候，先确认他指的是哪一层。

### 循环本体

`dugentx/providers/agent_loop_basic.py` 的 `BasicAgentLoop` 是 `ctx.agentLoop` 的默认实现。下面按真实顺序转写：

```python
async def run_turn(self, agent, prompt):
    session, events = agent.session, self.ctx.events
    result = TurnResult()

    session.append("turn/start", prompt=prompt)
    self._sync_system_prompt(agent)
    session.append("user/message", content=prompt)
    events.emit("turn/start", prompt)

    try:
        result = await self._steps(agent, result)
    except Exception as exc:                 # 循环自己出错也不许崩掉整个进程
        events.emit("agent/error", "agent-loop", exc)
        result.stop_reason = "error"
        result.text = f"（这一轮出错了：{type(exc).__name__}: {exc}）"
    finally:
        session.append("turn/end", stop_reason=result.stop_reason, steps=result.steps,
                       tool_calls=result.tool_calls, total_tokens=result.usage.total_tokens)
        events.emit("turn/end", result)
    return result


async def _steps(self, agent, result):
    tools = self.ctx.service("tools")
    for step_index in range(agent.config.max_steps):
        events.emit("step/start", step_index)
        session.append("step/start", index=step_index)

        self._sync_system_prompt(agent)
        messages = derive_view(session)
        messages = await events.waterfall("agent/pre-step", messages, terminal=_identity)
        verify_projection(messages, session)

        request = LlmRequest(
            model=agent.config.model or self._default_model(),
            messages=messages,
            tools=agent.visible_tools(),
            temperature=agent.config.temperature,
            max_tokens=None,
            reasoning_effort=agent.config.reasoning_effort,
        )
        request = await events.waterfall("agent/request", request, terminal=_identity)

        turn = await self._call_model(agent, request)
        session.append("assistant/message", content=turn.content, reasoning=turn.reasoning,
                       tool_calls=[{"id": c.id, "name": c.name, "arguments": c.arguments}
                                   for c in turn.tool_calls],
                       prompt_tokens=turn.usage.prompt_tokens,
                       completion_tokens=turn.usage.completion_tokens,
                       total_tokens=turn.usage.total_tokens)
        result.steps += 1
        result.usage = result.usage + turn.usage
        events.emit("step/end", step_index, len(turn.tool_calls))
        session.append("step/end", index=step_index, tool_calls=len(turn.tool_calls))

        if not turn.tool_calls:              # 这才是退出条件
            result.text = turn.content.strip()
            result.stop_reason = "completed"
            return result

        for call in turn.tool_calls:
            outcome = await tools.execute(call)
            session.append("tool/result", call_id=outcome.call_id, name=outcome.name,
                           content=outcome.to_text(), ok=outcome.ok, blocked=outcome.blocked)
            result.tool_calls += 1

    result.stop_reason = "max-steps"
    result.text = result.text or "（步数用完了，任务还没收口）"
    return result
```

一次模型对话本身走 `llm/stream`：

```python
registry = self.ctx.service("llm")
adapter = registry.adapter()

async def terminal(req):
    return adapter.stream(req)

stream = await events.waterfall("llm/stream", request, terminal=terminal)

async def tapped():
    async for delta in stream:
        events.emit("llm/chunk", delta)
        yield delta

turn = await assemble(tapped())
events.emit("llm/usage", turn.usage)
```

`_sync_system_prompt` 在回合开头和每个 step 开头都跑一次，但**只在文本变了时**才追加一条 `system/message`（第一条是 `reason="series"`，中途变了是 `reason="change"`）。系统提示词不是常量：工具清单、技能目录、工作区路径都在里面，会变的东西更要留痕，否则重放这个会话时复现不出当时真正发出去的请求。每次都写一条又会把日志稀释成噪声，所以比对。

注意区分两个命名空间：`session.append("turn/start", ...)` 是往**会话日志**里写一条事件；`events.emit("turn/start", prompt)` 是往**总线**上发一条通知。名字撞在一起是有意的（同一个概念），但它们的校验、消费者、持久化方式完全不同。两者也不完全重合：`session/start` 和 `session/end` 只存在于会话日志的 `EventKind` 里，目录里**故意没有**它们——一个会话开始了是日志里记下的一条事实，没有谁需要拦下它，把它摆进总线目录只会造出一个永远不触发的监听器。

### 运行期换模型：两处状态，少改一处就出错

`ctx.models`（`dugentx/providers/model_switch.py` 的 `ModelSwitcher`）回答「这个会话现在在跟谁说」，
以及换一个。它挂在 `llm` 那一行上，因为这件事需要两样只有那一层才有的东西：怎么造适配器
（`ADAPTERS`）、provider 有哪些（`available_providers()`，最终来自唯一能 import `any_llm` 的那个文件）。

它值得在架构里占一段，是因为它踩着一个不对称。循环取模型名的写法是：

```python
model=agent.config.model or self._default_model()
```

而 `agent.config.model` 通常来自配置里写死的那一行。所以一次切换必须**同时**改两处：
`registry.default_model`（兜底）和 `agent.config.model`（首选）。只换适配器、不改后者，
后果是拿新 provider 去请求旧的模型名——报出来的错看着像「这家不支持这个模型」，
真相是我们自己没把状态改干净。`ModelSwitcher._install` 里那两行赋值不是可选的收尾动作，
是这件事的主要工作。

两条跟着这条不变量来的性质：`current()` **不缓存**，每次去注册表现问（缓存会让「界面上的名字」
和「真正发出去的请求」分叉，那是这类状态最难查的一种坏法）；一次切换本身是一笔**可撤销的注册**
（`dispose()` 收回来，卸载时自动跑），并往会话日志追加一条 `model/switched`——换了模型的会话，
事后要能查出「当时到底问了谁」。

### 三个刻意的选择

**退出条件是「这一轮没再请求工具」，不是「跑了 N 轮」。** `max_steps` 是熔断（默认 24），触顶时 `stop_reason = "max-steps"`。按轮数当完成判断会腰斩正常任务——一个要读五个文件的任务，在第 8 步时只是干到一半。

**每一步都重新从日志投影上下文**（`derive_view(session)`），而不是在内存里维护一个 messages 列表。慢一点，但「模型看到的」和「日志里记下的」不可能分叉——压缩、恢复会话、子 Agent、审计全都免费得到。

**异常不往上抛。** 循环自己的异常变成一条 `agent/error` 事件加一次 `stop_reason = "error"` 的回合；工具内部的异常在 `ToolRegistry.execute` 里变成一条 `ToolOutcome(ok=False, ...)` 回传给模型。harness 崩掉比模型答错严重得多：前者丢掉整个会话，后者只是这一轮白跑。

## 两条硬不变量

`scripts/check_boundaries.py` 把规则变成静态断言。它做三件事：

1. **只有 `dugentx/providers/llm_anyllm.py` 可以 import `any_llm`。** 常量是 `LLM_ADAPTER = Path("providers/llm_anyllm.py")`；其他文件里出现 `any_llm` 或 `any_llm.*` 的 import 都算违规。
2. **任何地方都不许 import 网络库。** 名单写死在 `FORBIDDEN_NETWORK` 里：`requests`、`httpx`、`aiohttp`、`urllib.request`、`urllib3`、`socket`、`http.client`、`websockets`，匹配 `name == banned` 或 `name.startswith(banned + ".")`。
3. **`dugentx/kernel/` 不许反向依赖。** 对 `relative.parts[0] == "kernel"` 的文件，import 目标以 `dugentx.seams` / `dugentx.providers` / `dugentx.tools` / `dugentx.plugins` 开头的都算违规。

三道检查都印在同一个退出码上：有任何一条违规就列出文件和原因并返回 1，否则打印一行通过信息。扫描用 `ast.walk`，所以**函数体内的 import 也算**——这条很关键，绕过缝的代码经常写在函数里。

它的边界也要说清楚：只扫 `dugentx/` 下的 `.py`（`tests/`、`examples/`、`scripts/` 不在范围内），只做 import 层面的判断（不检查 import 之后怎么用，也不管运行时动态 import），`any_llm` 之外的外部包不做限制。

### 能被断言的不变量，胜过写在评审意见里的不变量

规则写在 README 里只是一种意愿：它只在有人读到、并且记得、并且愿意为此拒绝一个 PR 的时候才生效。写成断言以后，它的成本从「每次评审都要有人想起它」变成「跑一次脚本」，而后者是零成本的，所以它每次都会被执行。

这也是这份代码反复出现的同一种手法：把「大家都知道要这样」翻译成一个会失败的检查。事件名必须在目录里（派发时校验）、工具参数必须有类型标注（`tool_from_function` 里报错）、配置里不认识的键必须报错（`config_from`、`_KNOWN`、`CONFIG_KEYS` 这几类检查）、压缩结果必须能从日志重建（`encode_drop` 里的断言）——都是同一个动作。

## 会话日志

### 只能追加

`SessionLog` 没有 `update`，没有 `delete`。`append(kind, **data)` 给出一条带自增 `seq` 和时间戳的 `SessionEvent`。

顺带纠正一处容易找错的地方：仓库里**没有 `dugentx/providers/session_store.py` 这个文件**。`SessionLog` 和 `JsonlSessionStore` 一起定义在 `dugentx/seams/session.py` 里，`dugentx/plugins/session.py` 负责建它、按配置的 `session_id` 决定「接着上次」还是「开新的」，并提供两个键：`ctx.session`（日志）与 `ctx.sessionStore`（存储）。

落盘的时机是回合边界（`FLUSH_ON = ("turn/end",)`），而且带一道闸：**没有 `turn/start` 就不写**。让 `dugentx plugins`、`dugentx sessions` 这种只读命令每启动一次都留一个空会话文件，「我有几个会话」会变成一个越问越错的数字。收尾那一次由卸载时的 flush 负责，所以清单里不需要 `session/end`——何况它是**日志种类**、不是总线事件，写在那里只会挂一个永远不触发的监听器。

`session` 插件本身不往日志里补任何字段：**写日志的永远是拥有那件事的代码**（循环写 `user/message`、`assistant/message`、`tool/result`、`step/*`、`system/message`；权限和插件各写自己那几条）。监听器只观察，只把「现在值得存一次」翻译成一次 `store.save(log)`。

### 投影规则

日志是唯一真相，模型看到的历史是**从它投影出来的**：`derive_messages(log) = _project(log.events())`。`_project` 的规则有两条，都来自「一次请求里只有一个系统提示词」这个事实：

1. **只取最后一条 `system/message`。** 系统提示词是拼出来的、会变（工具清单变了它就变），但历史里不该堆一排系统消息——当前那一条才是真的。所谓「最后一条」，代码里的实现是循环里不断覆盖 `latest_system`，注意它是**按日志顺序**的最后一条，而不是按消息角色挑的。
2. **它排在最前面**，顺序是 `[system, ...其余...]`。按事件顺序天然会变成 `[user, system, ...]`（用户消息在 `turn/start` 时就追加了，系统提示词到 step 开头才同步），而很多 provider 会直接拒绝这种请求。

哪些事件进入投影由 `MODEL_VISIBLE` 决定：

```python
MODEL_VISIBLE: frozenset[str] = frozenset(
    {"system/message", "user/message", "assistant/message", "tool/result", "context/injected"}
)
```

其余的都是生命周期或元数据。`_events_to_messages` 把 `user/message` 和 `context/injected` 都投影成 `user` 角色，把 `assistant/message` 连同它的工具请求一起投影成 `assistant`，把 `tool/result` 投影成 `tool`。

### 压缩是投影的一步，不是日志的改写

`derive_view(log)` 与 `derive_messages(log)` 的唯一区别是压缩。压缩不改写日志，它以一条 `context/compacted` 事件的形式存在：

```python
session.append("context/compacted",
               drop_before_seq=cutoff, summary=summary, dropped=result.dropped,
               summarized=result.summarized, before_tokens=..., after_tokens=...,
               budget_tokens=limit, note=result.note)
```

`derive_view` 据此重建：丢掉 `seq < drop_before_seq` 的模型可见事件，再把 `summary` 作为一条 `user` 消息插在系统提示词后面（`at = 1 if view and view[0].role == "system" else 0`），正文前面有一段明白的抬头，说明它替代了被丢掉的部分、可能已经失真。多次压缩只认**最后一条**：后一次摘要已经包含前一次的内容。

这条设计让三件事同时成立：压缩**可审计**（日志里看得见什么时候压过、丢了几条、摘要是什么）、**可回退**（换一个预算重新压一次，旧决定自动被后一条覆盖）、**可验证**（模型看到的每一句都能从日志重算出来）。

`dugentx/plugins/compaction.py` 里的 `encode_drop` 负责把一次压缩结果翻译成（切点，摘要）。它要求保留的那段尾巴**逐字等于**日志投影的一个后缀，翻不动就抛 `DuGentXError`——因为日志只能表达「丢掉一段前缀」，任何「就地改写还留着的消息」都重建不出来。这里没有「尽力而为」的降级：一个无法从日志重建的投影，会让「模型可见 ⟺ 已记录」当场变成空话。

压缩器本身（`dugentx/providers/compaction_basic.py`）只做三件事：剪枝工具结果、写摘要、截断，并且有一条底线——**一次「压缩」把窗口变大是不许发生的**，摘要比它替代的内容还大时它会退回去只截断，并把这件事写进 `note`。

### `verify_projection` 在每一个 step 之前

```python
messages = derive_view(session)
messages = await events.waterfall("agent/pre-step", messages, terminal=_identity)
verify_projection(messages, session)
```

`verify_projection` 比较长度和逐条的角色与内容，不一致就抛 `DuGentXError`。规则是「模型可见的，就是已记录的」，而它放在**请求之前**——规则的价钱在执行点付才便宜。

压缩插件自己也调一次：`context/compacted` 写进日志之后，它用 `derive_view` 重算一遍，逐条确认新投影和压缩结果相等，最后再跑一次 `verify_projection`。规则写在文档里三个月后就会有人绕过；写成这个函数里的两行断言，绕不过去。

## 相对 dsh 刻意简化的地方，以及代价

`README.md` 有一张简化项的表，这里补上每一处的**后果**——简化从来不免费。

| 简化 | 后果 |
|---|---|
| **不做响应式 `inject`**。Cordis 的服务晚到会等待唤醒；DugentX 在装载前拓扑排序，缺服务当场失败 | 装载顺序在组合阶段就定死了。「等一个服务出现」这种插件写不出来，只能用 `ctx.get(key)` 做软依赖、到调用时再取。`ChannelApproval(lambda: ctx.get("human"))` 就是这个代价的产物：审批只在真要问人的时候才需要通道，所以它延迟取，而不是在自己的 `inject` 里要求 `human` |
| **配置只留一层**。`extends` + 一份 `plugins` 列表 + 按 id 的 `patch`，取代 profile → bundle → patch 三层 | `extends` 只解析一条链，深度上限 8 层，成环报错。`patch` 是**整行 config 替换**，不是深合并——所以「删掉一个键」必须把余下的键重写一遍。深合并被放弃，是因为「删一个键」在深合并里说不清楚 |
| **一个包，按缝分模块**。不是每个缝一个可安装包 | 包边界不再免费提供依赖纪律，得靠 `scripts/check_boundaries.py` 补。这也是那个脚本存在、并且要盯着 kernel 反向依赖的原因。（插件包的形态是**给第三方分发插件**用的，见「内核」一节；它不改变本体是一个包这件事。） |
| **会话格式不设版本与迁移链**。JSONL，一行一个事件 | `SessionEvent.from_json` 只认 `seq` / `kind` / `at` / `data` 四个键，没有 version 字段。改了某个事件 `data` 的字段名，旧日志不会报错，只会投影出不一样的历史。这里没有历史包袱要背，迁移机制的价值为零；代价是「格式发布之后不该改字段名」只能靠人记住 |

没有简化掉的：运行期挂载与卸载、派发时的事件契约校验、可替换的循环本身（`ctx.agentLoop` 换成别的实现就是另一个产品）。

## 诚实的限制

下面每一条都能在代码里指出来，不是猜测。

- **`dugentx/seams/__init__.py` 的 docstring 说「11 个缝」而且没有列 `human`，但目录里有 12 个缝，`human` 是其中之一**（`human.py` 自己的 docstring 写的是「这是第 12 个缝」）。这份文档和 `docs/seams.md` 以代码为准。
- **`verify_projection` 只比角色和内容**：`if got.role != want.role or got.content != want.content`。`tool_calls` 和 `tool_call_id` 不在比较范围里，所以一条「角色和正文一样、工具调用不一样」的消息能通过校验。
- **`tools/execute` 是被刻意不设的事件。** 目录里只有 `tools/pre-execute` 和 `tools/post-execute` 两个瀑布；工具本身的执行是 `pre-execute` 那个瀑布的 `terminal` 处理器。`dugentx/events.py` 在原来那一行留了注释说明理由：只有让工具执行当「最内层」，`nxt()` 才有能力决定放不放行；再开一个「执行」事件只会得到一个没人发、订阅了也不响的钩子。
- **事件目录只校验名字与方式，不校验载荷。** 传错载荷对象要等监听器里才炸。
- **目录为空的总线不做任何校验**（`EventBus._check` 开头是 `if not self._catalog: return`）。`AgentRuntime` 建的总线带目录，插件自己 `EventBus()` 建的不带。
- **`shell` 的黑白名单是护栏，不是安全边界。** 代码里写明了：真正的边界是操作系统权限和沙箱。
- **提示词里的技能目录是装载时缓存的。** `SkillProvider.catalog()` 是异步的，而 `PromptRegistry` 的 `render` 是同步的，所以 `prompt` 插件在装载时 `await` 一次、把行缓存下来。会话中途挂上技能，技能的**工具**当场可用，那份目录清单要等 `prompt` 插件被卸载重挂才更新。工具清单相反——它每次装配都现读注册表，所以挂上来的工具立刻出现在提示词里。
- **缝里有三处声明没有任何调用点**：`dugentx/seams/tools.py` 的 `ToolRunner` Protocol、`dugentx/seams/prompt.py` 的 `from_config()`、`dugentx/seams/agent.py` 的 `HookFn`。全仓库（含 `tests/`）只有定义，没有使用。按「只有实现没有消费者是死代码」的标准，这三个是死代码。留着是因为它们各自描述了缝的一种正当用法，但要诚实地说：目前没人用。
