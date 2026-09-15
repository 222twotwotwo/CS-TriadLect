"""能力缝（capability seam）：服务定义层。

一个**完整的缝**有三个角色，缺一不可（这条照抄 dsh）：

| 角色 | 是什么 | 在哪 |
|---|---|---|
| Service Definition | 接口与数据词汇 | 本目录 |
| Service Provider | 具体实现 | `dugentx/providers/` |
| Consumer | 用它的东西，通常是模型可调用的工具 | `dugentx/tools/` |

只有接口没有实现，是设计稿；只有实现没有消费者，是死代码。
这个目录里全是接口，所以它读起来应该像一份「这家 harness 认哪些能力」的清单。

12 个缝：

**内核**：`llm`（模型）、`tools`（工具与执行管道）、`agent`（会话与循环）、
`session`（事件日志与投影）、`prompt`（系统提示词拼装）

**动手**：`fs`（文件）、`shell`（命令）、`permissions`（权限分级）、
`human`（人机通道：问一句、选一个、告知一声）

**进阶**：`compaction`（上下文压缩）、`subagent`（子 Agent）、`skill`（按需技能）

另外还有一个不属于缝的共用模块：`messages` 是上面几条缝共用的消息词汇层。
它没有服务键、没有实现、也没有消费者——它只是一组类型，所以不占缝的名额。
"""

from dugentx.seams import (
    agent,
    compaction,
    fs,
    human,
    llm,
    messages,
    permissions,
    prompt,
    session,
    shell,
    skill,
    subagent,
    tools,
)

__all__ = [
    "agent",
    "compaction",
    "fs",
    "human",
    "llm",
    "messages",
    "permissions",
    "prompt",
    "session",
    "shell",
    "skill",
    "subagent",
    "tools",
]
