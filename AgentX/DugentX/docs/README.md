# DugentX 文档

DugentX 是一个示范用的 Agent harness：用 Python 写的可拔插模型，
体量小到能读完，但**该硬的地方没有软化**——事件契约会校验、边界能被断言、
每条注册都能撤销。

仓库根目录的 [README](../README.md) 是第一入口。这里放更细的东西。

---

## 按你的目的挑

| 你想 | 读 | 大概长度 |
|---|---|---|
| 知道这东西怎么设计的、为什么这么设计 | [architecture.md](architecture.md) | 15 分钟 |
| 查某条缝有什么方法、可以被谁换掉 | [seams.md](seams.md) | 查阅用 |
| 知道事件能挂在哪、谁在听 | [events.md](events.md) | 查阅用（自动生成） |
| 改配置：换模型、关工具、加插件 | [configuration.md](configuration.md) | 查阅用 |
| 自己写一个插件 | [writing-a-plugin.md](writing-a-plugin.md) | 20 分钟，有完整例子（模块与包两种形态） |
| 看那个编码 TUI 是怎么长出来的、怎么启动 | [tui.md](tui.md) | 10 分钟 |

## 四条建议的路线

**想理解「可拔插」到底指什么** —— 按这个顺序，中间不要跳：

1. [architecture.md](architecture.md) 的「内核」与「注册即 effect」两节
2. `dugentx/kernel/context.py` 与 `effect.py`（两页，是全部）
3. [events.md](events.md) 的 `waterfall` 一节，然后看 `dugentx/plugins/self_extension.py`
4. [seams.md](seams.md)，挑 `tools` 一条读完

**想加一个能力** —— [writing-a-plugin.md](writing-a-plugin.md) 走一遍，
再回来看 [seams.md](seams.md) 找你该用哪条缝。

**想把插件发给别人** —— 同一个文件的第 6 节把插件做成一个可安装的包（entry point + 清单），
模板是 `examples/dugentx-plugin-clock/`；配置侧写包名的规则在
[configuration.md](configuration.md) 的「`plugin:` 怎么解析」一节，
`uv run dugentx plugins --available` 能列出这台机器上装了哪些插件包。

**想让模型接上别家模型** —— [configuration.md](configuration.md) 的
「模型」一节：改三行，别的都不用动。

**想给它一个界面** —— [tui.md](tui.md)。它同时是一个完整例子：
怎么只靠一条缝（`human`）就把界面换掉，而循环、工具管道、审批一行都不改。

---

## 关于这些文档本身

- **[events.md](events.md) 是生成的**，源是 `dugentx/events.py` 的事件目录。
  改了事件不重新生成，`uv run python scripts/gen_docs.py --check` 会失败。
  手写的表迟早会烂，而一张烂掉的表比没有表更糟：它会静静地骗你。
- 其余几篇是人写的，所以它们**可能过期**。看到一个和你读到的不一致的地方，
  以代码为准，然后改文档——这个仓库里没有「文档是别人负责的」这回事。

## 名词对照

| 中文 | 代码里的名字 | 一句话 |
|---|---|---|
| 缝 | `seams/` | 一条能力的接口与词汇（只有定义，没有实现） |
| 实现 | `providers/` | 缝的具体实现，比如接哪家模型、怎么读 stdin |
| 消费者 | `tools/` `plugins/` | 用服务的东西，通常是模型可调用的工具 |
| 插件 | `plugins/` | 装载单元：把服务与工具接起来，并声明自己的依赖 |
| 清单 | `PluginManifest` | 插件包的自我声明：要什么、给什么、按哪版接口写的。读它**不执行**插件 |
| 注册表 | `PluginRegistry` | 已安装插件包的名单（meta 接口）。`dugentx plugins --available` 的来源 |
| 组合 | `Composition` | 一份配置装载之后的样子 |
| 卸载 | unmount | 把插件的每条注册按相反顺序撤销 |
| 投影 | `derive_view` | 从事件日志推出「模型此刻看到的历史」 |
