"""工具包 —— 每个模块装一组同源的工具，各自声明自己需要哪些服务。

一个工具模块对外只有三件东西：几个处理函数、一个 `register(ctx)`（返回能把
它们一次撤掉的 disposer）、以及一个 `@define_plugin` 工厂 `create(config)`。
配置里给每个模块一行，装载顺序由 `inject` 决定：装了 `fs` 才有 `ctx.fs`，
有了 `ctx.fs` 才注册得上 `read_file`。这层依赖交给配置表达，而不是写死在
一个「把所有工具都注册一遍」的函数里。

处理函数的第一个参数是 `ctx`，由工具注册表按参数名注入——模型看不到它，
`tool_from_function` 也不会把它写进 schema。其余参数就是模型要填的表单：
签名给类型，docstring 的 Args 段给说明，schema 由这两样长出来。
"""
