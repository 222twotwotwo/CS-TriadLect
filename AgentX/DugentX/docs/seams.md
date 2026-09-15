# 能力缝参考

一个**完整的缝**有三个角色，缺一不可（这条照抄 dsh）：

| 角色 | 是什么 | 在哪 |
|---|---|---|
| Service Definition | 接口与数据词汇 | `dugentx/seams/` |
| Service Provider | 具体实现 | `dugentx/providers/` |
| Consumer | 用它的东西，通常是模型可调用的工具 | `dugentx/tools/`（和 `dugentx/plugins/`） |

> 只有接口没有实现是设计稿；只有实现没有消费者是死代码。

下面每一节的代码块里，类型、字段、签名都照抄 `dugentx/seams/` 的源码；行尾用 `#` 跟的中文说明是为了读起来方便加的注解，源码里没有它们。

`dugentx/seams/` 里一共有 13 个能力模块加一个 `__init__.py`，其中 **12 个是缝**，`messages.py` 是它们共用的词汇层（`Message` / `ToolCall` / `Usage` / `Delta` / `AssistantTurn`），不算缝。`__init__.py` 的 docstring 里写的是「11 个缝」并且漏了 `human`，那是句没跟上代码的旧话。

所以下面这张表是 `__init__.py` 的 `__all__` 全貌：**12 个缝 + 1 个词汇层**，按列表顺序。

| 模块 | 服务键 | 一句话 |
|---|---|---|
| `agent` | `ctx.agents` / `ctx.agent` | 会话载体，以及驱动它的循环（`ctx.agentLoop`） |
| `compaction` | `ctx.compaction` | 上下文预算满了怎么办 |
| `fs` | `ctx.fs` | 文件访问 + 路径策略 |
| `human` | `ctx.human` | 人机通道：stdio、TUI、CI 自动回答都是它的实现 |
| `llm` | `ctx.llm` / `ctx.models` | 模型适配器注册表 + 运行期换模型；唯一允许依赖 any-llm 的地方 |
| `messages` | （无服务键，不是缝） | 十二条缝共用的消息词汇 |
| `permissions` | `ctx.permissions` / `ctx.approval` | 三档权限，开关在代码侧 |
| `prompt` | `ctx.prompt` | 系统提示词按段注册、按序拼装 |
| `session` | `ctx.session` / `ctx.sessionStore` | 只能追加的事件日志，以及从它投影出的模型历史 |
| `shell` | `ctx.shell` | 命令执行，带超时与黑白名单 |
| `skill` | `ctx.skills` | 按需加载的说明文档 |
| `subagent` | `ctx.subagents` | 把一件事整个交出去 |
| `tools` | `ctx.tools` | 工具注册表 + 四段执行管道 |

---

## `agent`

- **服务键**：`ctx.agent`（这次组合里的主 agent）、`ctx.agents`（活着的 agent 名单）。`ctx.agentLoop` 由 `dugentx/providers/agent_loop_basic.py` 提供，是这条缝的默认 driver。
- **协议**：

```python
class AgentDriver(Protocol):
    """`ctx.agentLoop` —— 循环本身也是一个可替换的服务。"""
    async def run_turn(self, agent: Agent, prompt: str) -> TurnResult: ...
```

- **数据词汇**：

```python
@dataclass(slots=True)
class AgentConfig:
    model: str = ""
    max_steps: int = 24                  # 熔断，不是退出条件
    temperature: float | None = None
    reasoning_effort: str | None = None
    tool_allow: tuple[str, ...] = ()     # 非空即白名单
    tool_deny: tuple[str, ...] = ()
    prompt_extras: dict[str, str] = field(default_factory=dict)
    context_budget_tokens: int = 60_000
    label: str = "main"

@dataclass(slots=True)
class TurnResult:
    text: str = ""
    steps: int = 0
    tool_calls: int = 0
    usage: Usage = field(default_factory=Usage)
    stop_reason: str = "completed"        # completed / max-steps / empty / error

    @property
    def ok(self) -> bool                  # stop_reason == "completed"

HookFn = Callable[[Agent], Awaitable[None] | None]
```

`Agent` 自己不是 Protocol，是这条缝里的具体类（消费方直接用它）：

```python
class Agent:
    def __init__(self, ctx: Context, session: SessionLog, config: AgentConfig) -> None
    async def send(self, prompt: str) -> TurnResult     # 转给 ctx.service("agentLoop")
    def inject(self, text: str) -> None                 # 追加 context/injected
    def visible_tools(self) -> list[dict[str, Any]]     # 白名单/黑名单在这里生效
    def close(self) -> None

class AgentRegistry:
    def __init__(self, ctx: Context) -> None
    def create(self, *, session: SessionLog | None = None,
               config: AgentConfig | None = None, label: str = "main") -> Agent
    def get(self, session_id: str) -> Agent | None
    def all(self) -> list[Agent]
    def register(self, agent: Agent) -> Disposer
```

- **实现**：`dugentx/plugins/agent.py`（提供 `agents` 与 `agent`，给主 agent 建一个 `ctx.child("agent:main")`）；`dugentx/providers/agent_loop_basic.py`（`BasicAgentLoop`，提供 `agentLoop`，`inject=("llm", "tools", "session")`）。
- **消费者**：`dugentx/providers/agent_loop_basic.py`（`run_turn(agent, prompt)`）；`dugentx/providers/subagent_inprocess.py`（用 `ctx.agents` 创建并摘除子 agent）；`dugentx/plugins/compaction.py`（软依赖：`_model_hint` 从当前 agent 或 `ctx.get("agents")` 拿模型名，预算从 agent 的 `context_budget_tokens` 拿）；`dugentx/providers/compaction_basic.py`（拿到的模型名传给 `compact(..., model=...)` 去写摘要）；`dugentx/plugins/self_extension.py`（`ctx.get("agent")`，把挂载记进会话）；`dugentx/plugins/tui.py`（`inject=("agent",)`）；`dugentx/tui/app.py`、`dugentx/cli.py`。
- **换一个实现**：写一个满足 `AgentDriver` 的类（一个 `run_turn` 方法），照 `agent_loop_basic.py` 的写法用 `@define_plugin("agentLoop", inject=("llm", "tools", "session"), provides=("agentLoop",))` 提供 `agentLoop`，再把 `dugentx.yml` 里 `agent-loop` 那一行的 `plugin:` 换掉。循环以外的任何东西都不用动。

## `compaction`

- **服务键**：`ctx.compaction`。
- **协议**：

```python
@runtime_checkable
class Compactor(Protocol):
    """`ctx.compaction`。"""
    async def compact(
        self,
        ctx: Context,
        messages: list[Message],
        *,
        budget_tokens: int,
    ) -> CompactionResult: ...
```

- **数据词汇**：

```python
@dataclass(slots=True)
class CompactionResult:
    messages: list[Message]
    dropped: int = 0
    summarized: int = 0
    before_tokens: int = 0
    after_tokens: int = 0
    note: str = ""
    kept_head: list[Message] = field(default_factory=list)   # 压缩后仍保留的最早几条

    @property
    def changed(self) -> bool            # dropped > 0 or summarized > 0
```

两个模块级函数也是这条缝的一部分：

```python
def estimate_tokens(text: str) -> int
def messages_tokens(messages: list[Message]) -> int
```

- **实现**：`dugentx/providers/compaction_basic.py` 的 `BasicCompactor`（剪枝 → 摘要 → 截断）。它的 `compact` 比协议多一个关键字参数 `model: str = ""`（摘要用哪个模型），由 `dugentx/plugins/compaction.py` 从当前 agent 的配置里取。
- **消费者**：`dugentx/plugins/compaction.py`（`ctx.on("agent/pre-step", ...)`，超预算才压缩，并把这次决定翻译成一条 `context/compacted`）；`dugentx/tools/skill_tools.py` 与 `dugentx/providers/skill_filesystem.py` 用 `estimate_tokens` 给技能正文估 token；`dugentx/plugins/prompt.py` 不用它。
- **换一个实现**：写一个满足 `Compactor` 的类，`from_config` 里对不认识的键报错，然后用 `ctx.provide("compaction", compactor)` 提供它，并把 `dugentx.yml` 里 `compaction` 那一行的 `plugin:` 换掉。注意 `dugentx/plugins/compaction.py` 里的 `encode_drop` 有硬约束：压缩结果保留的那段尾巴必须**逐字等于**日志投影的一个后缀，只能丢前缀。

## `fs`

- **服务键**：`ctx.fs`。
- **协议**：

```python
@runtime_checkable
class FileSystem(Protocol):
    """`ctx.fs`。所有方法都按工作区相对路径工作。"""
    root: Path
    async def read_text(self, path: str) -> str: ...
    async def write_text(self, path: str, content: str) -> None: ...
    async def edit(self, path: str, old: str, new: str, *, replace_all: bool = False) -> int: ...
    async def list_dir(self, path: str = ".") -> list[FileEntry]: ...
    async def exists(self, path: str) -> bool: ...
    def absolute(self, path: str) -> str: ...
```

- **数据词汇**：

```python
@dataclass(slots=True)
class FileEntry:
    path: str
    is_dir: bool
    size: int = 0
    def render(self) -> str              # 目录带尾斜杠，文件带 (字节数B)
```

以及路径策略的唯一判定点：

```python
class Workspace:
    def __init__(self, root: str | Path, *, extra_roots: list[str | Path] | None = None) -> None
    def resolve(self, path: str, *, must_exist: bool = False) -> Path   # 越界抛 ToolError
    def relative(self, path: Path) -> str
```

- **实现**：`dugentx/providers/fs_local.py` 的 `LocalFileSystem`（`DEFAULT_MAX_BYTES = 1_000_000`，`DEFAULT_READ_MAX_BYTES = 2_000_000`），由 `dugentx/plugins/fs.py` 装配；配置键是 `root`（必填）、`extra_roots`、`max_bytes`、`read_max_bytes`。
- **消费者**：`dugentx/tools/fs_tools.py`（`read_file` / `list_dir` / `search_text` / `write_file` / `edit_file`，`inject=("tools", "fs")`）；`dugentx/plugins/prompt.py` 的 `workspace_renderer`（软依赖，`ctx.get("fs")` 的 `root` 用来渲染「工作目录」那一段）。
- **换一个实现**：写一个满足 `FileSystem` 的类，在 `__init__` 里建好 `Workspace`，用 `ctx.provide("fs", ...)` 提供它，然后换掉 `dugentx.yml` 里 `fs` 那一行的 `plugin:`。工具不认识 `LocalFileSystem` 这个名字，只认 `ctx.fs`。

## `human`

- **服务键**：`ctx.human`。TUI 那一行同时还提供 `ctx.tui`（界面对象本身），但那是 UI 的入口，不是这条缝的一部分。
- **协议**：

```python
@runtime_checkable
class HumanChannel(Protocol):
    """`ctx.human` —— 此刻这台机器上，人是怎么跟 agent 说话的。"""
    name: str
    def interactive(self) -> bool
    async def ask(self, question: Question) -> Answer: ...
    async def choose(self, question: Question) -> Answer: ...
    def note(self, text: str, *, kind: NoteKind = "info") -> None: ...
```

`note` 故意不是 async：告知不该让调用方等。

- **数据词汇**：

```python
NoteKind = Literal["info", "success", "warn", "error", "dim", "tool"]

YES = "y"
NO = "n"
ALWAYS = "a"                             # 「这个工具别再问了」；它必须存在

@dataclass(slots=True)
class Choice:
    key: str
    label: str
    is_default: bool = False
    @staticmethod
    def yes() -> Choice: return Choice(YES, "允许这一次")
    @staticmethod
    def no() -> Choice: return Choice(NO, "拒绝", is_default=True)
    @staticmethod
    def always() -> Choice: return Choice(ALWAYS, "本会话内都允许")

APPROVAL_CHOICES: tuple[Choice, ...] = (Choice.yes(), Choice.always(), Choice.no())

@dataclass(slots=True)
class Question:
    prompt: str
    detail: str = ""
    choices: tuple[Choice, ...] = ()     # 空表示自由文本
    title: str = ""
    @property
    def is_choice(self) -> bool
    def default_key(self) -> str

@dataclass(slots=True)
class Answer:
    text: str = ""
    cancelled: bool = False
    source: str = ""                     # stdio / tui / policy
    @property
    def key(self) -> str                 # text.strip().lower()
    def __bool__(self) -> bool
```

- **实现**：`dugentx/providers/human_stdio.py` 的 `StdioHuman`（`name = "stdio"`；`__init__(*, stream=None, out=None, ask=None, allow_always=True)`），由 `dugentx/plugins/human.py` 装配；`dugentx/tui/app.py` 的 `TuiHuman`（`name = "tui"`），由 `dugentx/plugins/tui.py` 提供。
- **消费者**：`dugentx/providers/approval_channel.py` 的 `ChannelApproval`（把审批问题交给通道，由 `dugentx/plugins/permissions.py` 在 `mode: channel` 时装上）；`dugentx/tools/ask_tools.py` 的 `ask_human` 工具（`ctx.get("human")`，`LABEL_READ`）。
- **换一个实现**：写一个有 `name`、`interactive()`、`ask`、`choose`、`note` 的类，用 `ctx.provide("human", ...)` 提供它，然后换掉 `dugentx.yml` 里 `human` 那一行的 `plugin:`（`dugentx.tui.yml` 就是这么做的）。同一条缝两种实现共用一个服务键，所以一份配置里只能有一个。

## `llm`

- **服务键**：`ctx.llm`（`LlmRegistry`，不是单个 adapter）、`ctx.models`（`ModelSwitcher`）。
  两个键由同一个插件提供（`dugentx/plugins/llm.py` 的 `provides=("llm", "models")`）：
  注册表管「有哪几个适配器」，`ctx.models` 管「现在用谁、换一个」。
- **协议**：

```python
@runtime_checkable
class LlmAdapter(Protocol):
    """模型适配器。只需要实现 `stream`。"""
    name: str
    def stream(self, request: LlmRequest) -> AsyncIterator[Delta]: ...
```

- **数据词汇与注册表**：

```python
@dataclass(slots=True)
class LlmRequest:
    model: str
    messages: list[Message]
    tools: list[dict[str, Any]] = field(default_factory=list)   # OpenAI 工具格式
    temperature: float | None = None
    max_tokens: int | None = None
    reasoning_effort: str | None = None
    def wire_messages(self) -> list[dict[str, Any]]

class LlmRegistry:
    def __init__(self, default: str | None = None, default_model: str | None = None) -> None
    def register(self, adapter: LlmAdapter, *, default: bool = False) -> Disposer
    @property
    def default_name(self) -> str
    def use(self, name: str) -> None
    def adapter(self, name: str | None = None) -> LlmAdapter
    def names(self) -> list[str]
```

拼装逻辑写在缝这一层，adapter 只管把 provider 的流翻译成 `Delta`：

```python
async def drain_stream(adapter: LlmAdapter, request: LlmRequest) -> AssistantTurn
async def assemble(chunks: AsyncIterator[Delta]) -> AssistantTurn
def parse_arguments(raw: str) -> dict[str, Any]     # 解析失败回 {}，不抛异常
```

### `ctx.models`：运行期换模型

`dugentx/providers/model_switch.py`：

```python
@dataclass(frozen=True, slots=True)
class ModelChoice:
    provider: str = ""
    model: str = ""
    adapter: str = ""
    def describe(self) -> str                  # provider/model，缺了就往回退

class ModelSwitcher:
    def providers(self) -> list[str]           # any-llm 认得的全部 provider 名
    def adapters(self) -> list[str]            # 已注册在 ctx.llm 里的适配器名
    def current(self) -> ModelChoice           # 循环真正会发出去的那一个
    def switch(self, *, provider: str, model: str,
               api_key_env: str | None = None,
               temperature: float | None = None) -> ModelChoice
    def use(self, name: str) -> ModelChoice    # 切到已注册的适配器，不新建
    def dispose(self) -> None                  # 收回换出去的每一笔注册
```

它**不在** `dugentx/seams/llm.py` 里，而在 `providers/model_switch.py`——因为换模型要用到「适配器怎么造」
这个只有实现侧才知道的东西。列在这条缝下面，是因为它和 `ctx.llm` 是同一个插件提供的两个键，
找接口的人一定会一起找。

三条它自己的性质，都值得写在缝这一层：

- **它不缓存。** `current()` 每次去 `ctx.llm` 现问。缓存会让「界面上的模型名」和「真正发出去的
  模型名」分叉，而分叉之后你会先怀疑模型、再怀疑网络，最后才怀疑那行缓存。
- **一次切换要改两个地方。** 循环取的是 `agent.config.model or registry.default_model`
  （`BasicAgentLoop._default_model()` 读的就是注册表上那个字段）：
  只把适配器换掉、不改 `agent.config.model`，新 provider 会收到旧模型名，报出来的错指向「模型」。
  `switch()` 同时写 `registry.default_model` 和 `agent.config.model`，并在会话日志里追加一条
  `model/switched`、在总线上 `emit("model/switched", provider, model)`。
- **provider 清单只能经过它。** `providers()` 那份清单最终来自 `dugentx/providers/llm_anyllm.py` 的
  `available_providers()`——所以要列「有哪些 provider 可选」只能经过这个服务，
  **只有那一个文件可以 import `any_llm`**。
- **实现**：`dugentx/providers/llm_anyllm.py` 的 `AnyLlmAdapter`（**全仓库唯一 import `any_llm` 的文件**）；`dugentx/providers/llm_replay.py` 的 `ReplayAdapter`（永不联网，`Scripted` 是它的子类，测试里的顺手写法）。两者都由 `dugentx/plugins/llm.py` 按配置键 `adapter`（`"anyllm"` / `"replay"`）装配；同一个插件再用 `ADAPTERS["anyllm"]` 造一个 `ModelSwitcher` 提供 `ctx.models`。
- **消费者**：`dugentx/providers/agent_loop_basic.py`（`ctx.service("llm").adapter()`，把流包在 `llm/stream` 瀑布里）；`dugentx/providers/compaction_basic.py`（软依赖 `ctx.get("llm")`，有模型就写摘要，没有就退到机械摘录）；`ctx.models` 目前只有界面层在用（它只调 `current()` / `switch()`，不自己造适配器），harness 本体不用它。
- **换一个实现**：实现 `stream` 与 `name`，在 `dugentx/plugins/llm.py` 的 `ADAPTERS` 里加一个 builder，然后写 `adapter: <你的名字>`。`ctx.models.switch()` 造的新适配器固定走 `anyllm` 那条 builder，所以要能被运行期切换，你的实现得在那条路上挂得上。别的地方想调模型必须经过 `ctx.llm`，这条由 `scripts/check_boundaries.py` 强制。

## `messages`

**这不是一条缝**，是十二条缝共用的词汇层，没有服务键、没有 provider。列在这里是因为它就在 `dugentx/seams/` 里，找接口的人一定会点开。

```python
Role = Literal["system", "user", "assistant", "tool"]

@dataclass(slots=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    @property
    def arguments_json(self) -> str
    def to_wire(self) -> dict[str, Any]

@dataclass(slots=True)
class Message:
    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    def to_wire(self) -> dict[str, Any]
    def text_of(self, limit: int = 200) -> str

@dataclass(slots=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    def __add__(self, other: Usage) -> Usage

@dataclass(slots=True)
class AssistantTurn:
    content: str = ""
    reasoning: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    def to_message(self) -> Message

@dataclass(slots=True)
class Delta:
    text: str = ""
    reasoning: str = ""
    tool_call_id: str | None = None
    tool_name: str | None = None
    arguments_delta: str = ""
    usage: Usage | None = None
    @property
    def is_tool_call(self) -> bool

def tool_result_message(call_id: str, content: str) -> Message
```

它存在的理由不是洁癖：wire 格式是外部契约、会变，harness 的内部词汇不该跟着变。翻译只发生在 `providers/llm_anyllm.py` 一个文件里。

- **消费者**：几乎所有模块。写服务定义时用它，写 adapter 时用它，写工具时用它，`derive_view` 投影出来的也是它。它是这套代码里唯一一个「所有人都认识」的类型集合。

## `permissions`

- **服务键**：`ctx.permissions`（`PermissionPolicy`）、`ctx.approval`（回答「这次能不能跑」的那一个）。
- **协议与数据词汇**：

```python
Level = Literal["auto", "confirm", "deny"]

@dataclass(slots=True)
class ApprovalRequest:
    tool: str
    labels: frozenset[str]
    arguments: dict[str, object]
    level: Level
    summary: str = ""
    agent_label: str = "main"
    def render(self) -> str

@dataclass(slots=True)
class Decision:
    allowed: bool
    level: Level
    reason: str = ""
    @classmethod
    def allow(cls, level: Level = "auto", reason: str = "") -> Decision
    @classmethod
    def refuse(cls, level: Level, reason: str) -> Decision

@runtime_checkable
class Approval(Protocol):
    """`ctx.approval` —— 谁来回答「这次能不能跑」。"""
    async def decide(self, request: ApprovalRequest) -> Decision: ...

@dataclass(slots=True)
class PermissionPolicy:
    levels: dict[str, Level] = field(default_factory=lambda: {
        LABEL_READ: "auto",
        LABEL_WRITE: "confirm",
        LABEL_NETWORK: "confirm",
        LABEL_DANGEROUS: "deny",
    })
    default: Level = "confirm"
    def level_for(self, labels: frozenset[str]) -> Level     # 取最严的那一档
    def describe(self) -> str

ApprovalHook = Callable[[ApprovalRequest], object]
```

`levels` 的默认字典用的是 `dugentx/seams/tools.py` 里的标签常量（`LABEL_READ` / `LABEL_WRITE` / `LABEL_NETWORK` / `LABEL_DANGEROUS`），不是字面量。

把门装到管道上的那一个函数：

```python
def install_gate(ctx: Context, *, policy: PermissionPolicy, approval: Approval) -> None
```

它内部是 `ctx.on("tools/pre-execute", gate, prepend=True)`：`level == "auto"` 时发 `permission/skip` 再 `return await nxt()`；否则 `await approval.decide(request)`、发 `permission/decided`，不通过就 `raise PermissionDenied(...)`。

- **实现**：`dugentx/providers/approval_policy.py` 的 `PolicyApproval`（`DEFAULT_ANSWERS = {"auto": True, "confirm": False, "deny": False}`，另有 `ArgumentRule(tool, contains, allowed, reason="")`）；`dugentx/providers/approval_interactive.py` 的 `InteractiveApproval`；`dugentx/providers/approval_channel.py` 的 `ChannelApproval`（`__init__(resolve: Callable[[], object | None], events: EventBus | None)`）。策略本身（`PermissionPolicy`）由 `dugentx/plugins/permissions.py` 直接构造，配置键是 `levels` / `default` / `mode` / `answers` / `tool_overrides` / `argument_rules`。
- **消费者**：`dugentx/plugins/permissions.py`（同时提供两个键并调用 `install_gate`）。`approval` 没有别的消费者——它是给外部钩子留的口子。
- **换一个实现**：写一个满足 `Approval` 的类（一个 `decide` 方法），在 `dugentx/plugins/permissions.py` 的 `mode` 分支里加一个分支并 `ctx.provide("approval", ...)`。要改的是「谁来回答」，分档规则留在 `PermissionPolicy` 里不动。

## `prompt`

- **服务键**：`ctx.prompt`。
- **协议与数据词汇**：

```python
Render = Callable[[Context], str]

@dataclass(slots=True)
class PromptSection:
    name: str
    render: Render
    order: int = 100
    title: str = ""

class PromptRegistry:
    def __init__(self) -> None
    def section(self, name: str, render: Render, *, order: int = 100, title: str = "") -> Disposer
    def assemble(self, ctx: Context) -> str          # 按 (order, name) 排序，空段落跳过
    def names(self) -> list[str]

def static(text: str) -> Render
def from_config(key: str, default: str = "") -> Render
```

- **实现**：`dugentx/plugins/prompt.py`（`inject=("tools",)`）。它注册五段：`persona`（order 10）、`workspace`（20）、`skills`（30）、`tools`（40）、`extras`（90）。
- **消费者**：`dugentx/providers/agent_loop_basic.py` 的 `_sync_system_prompt`（`ctx.get("prompt")`，`prompt.assemble(agent.ctx).strip()`，只在文本变了时追加一条 `system/message`）。`from_config` 全仓库没有调用点（它是死代码，见 `docs/architecture.md` 最后一段）。
- **换一个实现**：段落的注册是 `ctx.effect(...)` 包起来的，所以任何插件都能往同一个注册表里加段，也能用 `ctx.provide("prompt", 你自己的注册表)` 整个换掉。换掉的要求只有一条：`assemble(ctx) -> str` 是同步的，因为它在循环里每步被调用。

## `session`

- **服务键**：`ctx.session`（`SessionLog`）、`ctx.sessionStore`（持久化）。
- 注意 `EventKind` 里的 `session/start` 与 `session/end` **只存在于会话日志**：`dugentx/events.py` 的总线目录里故意没有它们（那里留了注释说明理由），所以它们不会被 `ctx.on(...)` 订阅到。名字相同但属于两个命名空间的事件是 `turn/start`、`turn/end`、`step/start`、`step/end`、`context/compacted`、`plugin/mounted`、`plugin/unmounted`、`skill/loaded` 这一批——日志里记一条，总线上可能同时发一条。
- **数据词汇**：

```python
EventKind = Literal[
    "session/start", "turn/start", "turn/end", "step/start", "step/end",
    "system/message", "user/message", "assistant/message",
    "tool/request", "tool/result", "context/injected", "context/compacted",
    "plugin/mounted", "plugin/unmounted", "skill/loaded", "session/end",
]

MODEL_VISIBLE: frozenset[str] = frozenset(
    {"system/message", "user/message", "assistant/message", "tool/result", "context/injected"}
)

@dataclass(slots=True)
class SessionEvent:
    seq: int
    kind: str
    at: float
    data: dict[str, Any] = field(default_factory=dict)
    def to_json(self) -> dict[str, Any]
    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> SessionEvent
```

- **两个类，一个是值对象、一个是 provider**：

```python
class SessionLog:
    def __init__(self, session_id: str | None = None, *, events: list[SessionEvent] | None = None) -> None
    def append(self, kind: EventKind | str, **data: Any) -> SessionEvent
    def events(self) -> list[SessionEvent]
    def of_kind(self, *kinds: str) -> list[SessionEvent]
    def last(self, kind: str) -> SessionEvent | None
    def count(self, kind: str) -> int
    def usage(self) -> Usage
    def __len__(self) -> int
    def __iter__(self) -> Iterator[SessionEvent]

class JsonlSessionStore:
    def __init__(self, root: str | Path) -> None
    def path_for(self, session_id: str) -> Path
    def save(self, log: SessionLog) -> Path
    def append_event(self, session_id: str, event: SessionEvent) -> None
    def load(self, session_id: str) -> SessionLog
    def list_sessions(self) -> list[str]
```

`JsonlSessionStore` 是**这条缝里唯一一个和接口写在一个文件里的 provider**。仓库里没有 `dugentx/providers/session_store.py`。

- **投影函数**（缝的公开接口）：

```python
def derive_view(log: SessionLog) -> list[Message]      # 套用最后一条 context/compacted
def derive_messages(log: SessionLog) -> list[Message]  # 完整历史，不套压缩
def verify_projection(messages: list[Message], log: SessionLog) -> None
```

`derive_view` 的细节：`drop_before_seq` 之前的模型可见事件被丢掉，`summary` 作为一条 `user` 消息插在系统提示词之后。

- **实现**：`dugentx/plugins/session.py`（建 `JsonlSessionStore`，按 `session_id` 决定恢复还是新建，提供两个键，并在 `turn/end` 落盘；`FLUSH_ON = ("turn/end",)`——`session/end` 是日志种类而不是总线事件，挂在那里会是一条永远不触发的监听器，收尾那一次由卸载时的 flush 负责）。
- **消费者**：`dugentx/providers/agent_loop_basic.py`（写 `turn/start`、`user/message`、`system/message`、`step/*`、`assistant/message`、`tool/result`；读 `derive_view` + `verify_projection`）；`dugentx/plugins/compaction.py`（读日志、追加 `context/compacted`）；`dugentx/tools/skill_tools.py`（追加 `skill/loaded`）；`dugentx/plugins/self_extension.py`（追加 `plugin/mounted` / `plugin/unmounted`）；`dugentx/providers/subagent_inprocess.py`（给子会话落盘，`ctx.get("sessionStore")`）；`dugentx/plugins/agent.py`（提供主 agent 时 `ctx.service("session")`）；`dugentx/cli.py`（`dugentx replay` / `dugentx sessions`）。
- **换一个实现**：写一个有 `save` / `load` / `path_for` / `append_event` / `list_sessions` 的类，用 `ctx.provide("sessionStore", ...)` 提供它，然后换掉 `dugentx.yml` 里 `session` 那一行的 `plugin:`。`SessionLog` 本身是纯内存的值对象，换存储不动上层一行代码。

## `shell`

- **服务键**：`ctx.shell`。
- **协议**：

```python
@runtime_checkable
class Shell(Protocol):
    """`ctx.shell`。"""
    cwd: Path
    async def run(
        self,
        command: str,
        *,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
    ) -> ShellResult: ...
```

- **数据词汇**：

```python
@dataclass(slots=True)
class ShellResult:
    command: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration: float = 0.0
    timed_out: bool = False
    @property
    def ok(self) -> bool                      # exit_code == 0 and not timed_out
    def render(self, *, head: int = 60, tail: int = 20) -> str

@dataclass(slots=True)
class ShellPolicy:
    allow_prefixes: tuple[str, ...] = ()
    deny_substrings: tuple[str, ...] = (
        "rm -rf /", "mkfs", ":(){:|:&};:", "shutdown", "diskpart")
    notes: dict[str, str] = field(default_factory=dict)
    def check(self, command: str) -> str | None    # 返回拦截理由，或 None 放行
```

代码里写明了：`ShellPolicy` 是护栏，不是安全边界——真正的边界是操作系统权限和沙箱。

- **实现**：`dugentx/providers/shell_local.py` 的 `LocalShell`（`DEFAULT_TIMEOUT = 60.0`，`DEFAULT_MAX_OUTPUT_BYTES = 200_000`），由 `dugentx/plugins/shell.py` 装配；配置键是 `root` / `timeout` / `max_output_bytes` / `allow_prefixes` / `deny_substrings` / `env`。黑名单是**追加**，配置只能往上加，不能关掉默认那几条。
- **消费者**：`dugentx/tools/shell_tools.py`（`run_command`，`inject=("tools", "shell")`，标签 `LABEL_WRITE`，配置 `dangerous: true` 时改成 `LABEL_DANGEROUS`）。
- **换一个实现**：写一个有 `cwd` 与 `run` 的类，用 `ctx.provide("shell", ...)` 提供它，然后换掉 `dugentx.yml` 里 `shell` 那一行的 `plugin:`。要做「把执行世界搬到容器里」，就是换这一个类。

## `skill`

- **服务键**：`ctx.skills`。
- **协议**：

```python
@runtime_checkable
class SkillProvider(Protocol):
    """`ctx.skills`。"""
    async def catalog(self) -> list[SkillInfo]: ...
    async def load(self, name: str) -> str: ...
```

- **数据词汇**：

```python
@dataclass(slots=True)
class SkillInfo:
    name: str
    description: str
    path: str = ""
    when_to_use: str = ""
    tokens: int = 0
    def render(self) -> str             # "- 名字：描述（什么时候用：…）"
```

- **实现**：`dugentx/providers/skill_filesystem.py` 的 `FilesystemSkills`（`SKILL_FILENAME = "SKILL.md"`，`from_config(cls, config, *, events=None)`），由 `dugentx/plugins/skill.py` 装配；配置键 `directories`。目录不存在在装载时报错。
- **消费者**：`dugentx/tools/skill_tools.py` 的 `use_skill`（`inject=("tools", "skills", "session")`，标签 `LABEL_READ`，加载时往会话写 `skill/loaded`）；`dugentx/plugins/prompt.py` 的 `_read_skill_lines`（软依赖，装载时读一次目录并把行缓存下来）。
- **换一个实现**：写一个有 `catalog()` 与 `load(name)` 的类，用 `ctx.provide("skills", ...)` 提供它，然后换掉 `dugentx.yml` 里 `skill` 那一行的 `plugin:`。要让运行期新增的技能立刻出现在提示词里，得让 `prompt` 插件重新装载——目录段是装载时缓存的。

## `subagent`

- **服务键**：`ctx.subagents`。
- **协议**：

```python
@runtime_checkable
class SubagentProvider(Protocol):
    """`ctx.subagents`。"""
    async def spawn(self, ctx: Context, request: SubagentRequest) -> SubagentResult: ...
```

- **数据词汇**：

```python
@dataclass(slots=True)
class SubagentRequest:
    prompt: str
    label: str = "sub"
    model: str | None = None
    tool_allow: tuple[str, ...] = ()
    max_steps: int = 16
    def render(self) -> str

@dataclass(slots=True)
class SubagentResult:
    output: str
    session_id: str
    steps: int = 0
    usage: Usage = field(default_factory=Usage)
    stop_reason: str = "completed"
    def render(self) -> str              # 结论 + "（子 Agent … 用了 N 步）"
```

- **实现**：`dugentx/providers/subagent_inprocess.py` 的 `InProcessSubagent`（`CONFIG_KEYS = frozenset({"default_model", "max_steps", "tool_allow", "persist_sessions"})`），由 `dugentx/plugins/subagent.py` 装配（`inject=("agents",)`）。
- **消费者**：`dugentx/tools/subagent_tools.py` 的 `delegate_task`（标签 `LABEL_WRITE`——子 Agent 会在主会话的名义下动这个世界）。
- **换一个实现**：写一个有 `spawn(ctx, request)` 的类，用 `ctx.provide("subagents", ...)` 提供它，然后换掉 `dugentx.yml` 里 `subagent` 那一行的 `plugin:`。跨进程、跨产品的委托都只需要满足这一个方法。

## `tools`

- **服务键**：`ctx.tools`。
- **协议**：这条缝的接口是一个具体类而不是 `Protocol`（`ToolRegistry` 自己就是流水线的实现）：

```python
class ToolRegistry:
    def __init__(self, ctx: Context) -> None
    def register(self, tool: Tool) -> Disposer
    def get(self, name: str) -> Tool
    def names(self) -> list[str]
    def all(self) -> list[Tool]
    def specs(self) -> list[dict[str, Any]]
    async def execute(self, call: ToolCall) -> ToolOutcome

class ToolRunner(Protocol):
    """给那些只想拿一个「能跑工具的 callable」的地方用。"""
    async def __call__(self, call: ToolCall) -> ToolOutcome: ...
```

`ToolRunner` 全仓库没有调用点（死代码，见 `docs/architecture.md`）。

- **数据词汇**：

```python
LABEL_READ = "read"
LABEL_WRITE = "write"
LABEL_DANGEROUS = "dangerous"
LABEL_NETWORK = "network"

@dataclass(slots=True)
class ToolOutcome:
    call_id: str
    name: str
    content: str
    ok: bool = True
    blocked: bool = False
    note: str = ""
    def to_text(self) -> str      # 失败时前缀 "[已拦截] " 或 "[执行失败] "

@dataclass(slots=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., Any]
    labels: frozenset[str] = frozenset()
    title: str = ""
    def spec(self) -> dict[str, Any]      # OpenAI 的 function 格式
```

工具 schema 由函数自己长出来：

```python
def tool_from_function(
    fn: Callable[..., Any],
    *,
    name: str | None = None,
    labels: frozenset[str] = frozenset(),
    description: str | None = None,
) -> Tool
```

它要求参数有完整类型标注、有 docstring、docstring 里有 `Args:` 段；缺了就在**这里**报错。`ctx` / `call` 这两个参数名由注册表注入，不会被写进 schema。

管道在 `ToolRegistry.execute` 里的真实顺序：`tool/call`（emit）→ `tools/pre-execute`（waterfall，工具自己的处理器是它的 `terminal`）→ `tools/post-execute`（waterfall）→ `tool/result`（emit）。目录里**没有** `tools/execute`，那是刻意的——工具执行必须当「最内层」，`nxt()` 才有能力决定放不放行。

- **实现**：`dugentx/plugins/tools.py`（`ToolRegistry` 自己，`inject` 无，`provides=("tools",)`）。它不带任何工具。
- **消费者**：`dugentx/tools/fs_tools.py`、`shell_tools.py`、`skill_tools.py`、`subagent_tools.py`、`ask_tools.py`（各自 `ctx.service("tools").register(...)`）；`dugentx/seams/permissions.py` 的 `install_gate`（挂在 `tools/pre-execute` 上）；`dugentx/tui/paint.py`（挂在 `tools/pre-execute` / `tools/post-execute` 上画 diff）；`dugentx/plugins/prompt.py`（工具清单直接读注册表）；`dugentx/plugins/self_extension.py`（`manage_plugin` 工具的注册也走 `ctx.service("tools")`）；`dugentx/providers/agent_loop_basic.py`（`tools.execute(call)`）。
- **换一个实现**：写一个有 `register` / `all` / `specs` / `execute` 的类，用 `ctx.provide("tools", ...)` 提供它，然后换掉 `dugentx.yml` 里 `tools` 那一行的 `plugin:`。管道本身在这条缝里，换掉它同时换掉了权限门挂载的位置。

---

## 一个缝是怎么被换掉的

用 `human` 这条缝当例子。它在 `dugentx.yml` 里是这么装上的：

```yaml
  - id: human
    plugin: dugentx.plugins.human
    config:
      allow_always: true
```

`dugentx.tui.yml` 整份文件只做一件事——把这一行换掉。它 `extends` 基座配置，用 `patch` 按 id 覆盖：

```yaml
extends: dugentx.yml

patch:
  # stdio 通道和 TUI 通道提供的是同一个服务键（ctx.human），
  # 一个进程里只能有一个：同时有两张嘴问同一个人，只会把问题问乱。
  - id: human
    disabled: true

  # 这一行就是全部的新增。它提供 ctx.human（TUI 通道）和 ctx.tui（界面本身）。
  - id: tui
    plugin: dugentx.plugins.tui
    config:
      prompt: "› "
      show_banner: true
      history_limit: 200
      # 颜色留空表示自动判断：输出被重定向到文件时会自己关掉，
      # 免得把 ANSI 转义写进日志里。
      color: null
```

两点要读准：

- `disabled: true` 是**必须**的，不是可选的。两个插件都 `provides=("human",)`，而装载器在 `order_plugins` 里检查「服务被两个插件同时声明提供」并直接报错，`Context.provide` 也不允许覆盖已有的键。同一个键只能有一个提供者——这正是「换掉实现」的方式：改配置，不是叠加。
- `patch` 是整行 config 替换，不是深合并；`disabled` 只写 `true` 就是把这一行关掉，不需要重复 `plugin`。

换完以后，`permissions` 插件的 `mode: channel` 那一行一个字都没改：它的 `ChannelApproval(lambda: ctx.get("human"))` 拿到的是 TUI 通道。审批不知道那一句弹在哪，也不知道对面是终端还是界面。
