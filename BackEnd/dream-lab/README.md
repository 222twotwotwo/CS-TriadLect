# 🔬 逐梦实验室


<div align="center">

**这个世界还有很多副本没修好。**
**要不要来实验室，和我们一起当管理员？**

</div>

> 一间藏在服务器里的像素实验室，和一位失忆的管理员。
> 你将从零开始，把它崩溃的世界修好——顺便，第一次摸到「后端」是什么东西。

这不是一个教程，是一个**能跑起来的小游戏**。
但它是一个真的后端项目：真的服务器、真的数据库、真的 HTTP 请求、真的配置文件。
你玩通它，就等于亲手做了一遍后端工程师每天在做的那些事。

---

## 🚀 三步跑起来

**第一步：确认你有 Java 21**

```bash
java -version
```

看到 `21.x.x` 就对了。没装的话：

- macOS：`brew install --cask oracle-jdk@21`
- Windows：去 <https://www.oracle.com/java/technologies/downloads/#jdk21-windows>

**第二步：启动这个世界的服务器**

```bash
# macOS / Linux
./run.sh

# Windows
run.bat
```

第一次运行会自动下载依赖（需要联网，1~2 分钟），之后就是秒级启动。

**第三步：推开实验室的门**

浏览器打开 <http://localhost:8080>

---

## 🎮 这游戏怎么玩

这间实验室的服务器里住着一位管理员，叫 **星野凛**：她的记忆碎成了七片，
而这个靠她的记忆撑着运转的世界，正在崩塌。你要做的，就是一片一片帮她找回来。

**每一关，都是一个真实的后端知识点**，只是被包成了剧情：

| 碎片 | 剧情任务 | 你实际上学会的 |
| --- | --- | --- |
| 1 | 和星野凛说上第一句话 | 什么是 HTTP 请求、什么是接口 |
| 2 | 在源代码注释里找到隐藏的路由 | 读代码、看 API 文档 |
| 3 | 用正确的咒语参数敲开守门人的锁 | 请求参数、参数校验（还有 403） |
| 4 | 翻开样本库里的「访客记录」 | 数据库、ORM、日志与审计 |
| 5 | 在风暴之夜的监控日志里揪出异常 | 看日志排查问题 |
| 6 | 改一行配置，重启服务器 | 配置文件、重启服务 |
| 7 | 写一句话，永远留在她的世界里 | POST 写操作、持久化 |

**几个会"记住你"的时刻：**

- 世界修好的那一刻，画面会黑下来，郑重地告诉你你刚才做了什么
- 通关之后，那扇门会通向真实世界（链接在 `js/config.js` 里，随你换成自己的视频或招新页）
- 下次再打开这个网址，如果上次已经通关，会先问你一句：**继续逛**，还是**再来一次**

**两个操作入口，随你挑（它们本质上是同一件事）：**

- 页面上的按钮 —— 适合刚开始的你
- 底部的 **⌨ 终端** —— 直接敲 `GET /rin/hello`，像真正的工程师那样
- 你也可以最小化浏览器，打开系统终端跑 `curl http://localhost:8080/rin/hello`

> 卡住了？每一关都有递进的提示，星野凛和格兰特不会让你卡死。
> 迷路了就用你自己的编辑器打开项目里的源文件——历代管理员留下的批注（`//` 开头的中文）就是攻略。

---

## 🔍 这个后端到底在干什么

```
你的浏览器                    Spring Boot 服务器                    数据
┌──────────┐   HTTP 请求    ┌────────────────────┐   ORM    ┌──────────────┐
│  页面 /   │ ─────────────▶ │  Controller        │ ───────▶ │  SQLite      │
│  终端     │                │  （负责回应你）      │          │  data/       │
│           │ ◀───────────── │  Service / Mapper  │ ◀─────── │  world.db    │
└──────────┘   JSON 回应     └────────────────────┘          └──────────────┘
                                       ▲
                                       │ 启动时读一次
                              config/world.properties（世界规则）
```

这份代码按 **MVC 三层**分好了工，读起来可以一层一层看：

```
Controller（接请求）→ Service（写业务）→ Mapper（查数据库）→ Entity（表）
```

- **Controller** 只做三件事：收参数、调 Service、把结果变成 JSON
- **Service** 是真正干活的地方，所有"规则"都在这儿（比如"碎片要按顺序找"）
- **Mapper** 只负责跟数据库说话，连 SQL 都是方法名自动生成的

---

## 📁 代码地图

```
dream-lab/
├── run.sh / run.bat              一键启动脚本
├── config/world.properties       ★ 世界规则（第 6 关你要改的就是它）
├── data/world.db                 数据库文件（第一次运行后自动生成）
└── src/main/
    ├── java/lab_dream/
    │   ├── DreamLabApplication.java       启动入口（点火钥匙）
    │   ├── controller/                  ★ 第一层：接住请求、决定回什么
    │   │   ├── RinController.java           管理员的耳朵：第一句 hello
    │   │   ├── MemoryFragmentController.java ★ 咒语清单就写在文件顶部的注释里
    │   │   ├── ArchiveController.java       样本库：访客记录
    │   │   ├── MonitorController.java       监控中心：风暴日志
    │   │   ├── WorldController.java         世界稳定度
    │   │   ├── FinaleController.java        终章与通关证书
    │   │   ├── StoryController.java         剧情文本下发
    │   │   └── GameController.java          世界仪表盘 + 重置进度
    │   ├── service/                     ★ 第二层：真正的业务逻辑
    │   │   ├── StoryService.java            读 story.json
    │   │   ├── GameStateService.java        当前进度（第几片了）
    │   │   └── WorldRules.java              世界规则与稳定度
    │   ├── mapper/                      ★ 第三层：只跟数据库打交道
    │   │   ├── MemoryFragmentMapper.java    碎片表（方法名即 SQL）
    │   │   ├── JourneyMapper.java           足迹表
    │   │   └── WhisperMapper.java           悄悄话表
    │   ├── entity/                      三张表的实体（对象 ↔ 表行）
    │   │   ├── MemoryFragment.java / Journey.java / Whisper.java
    │   ├── dto/                         对外传输的形状（record）
    │   ├── config/                      装配与拦截器
    │   │   ├── WebConfig.java / JourneyInterceptor.java   足迹记录器
    │   │   └── WorldSeeder.java             开服时把七片碎片登记在册
    │   └── exception/                   业务异常与全局错误处理
    └── resources/
        ├── story/story.json             ★ 全部剧情、提示、日志卷宗
        └── static/                      前端（一个页面 + PixiJS 像素世界）
            ├── index.html
            ├── css/style.css
            ├── assets/
            │   ├── rin_idle_1..9.png       星野凛的待机动画（9 帧）
            │   ├── rin_emo_*.png           星野凛的对话立绘（正常/笑眯眯/难过/哭泣/脸红/说话）
            │   ├── grant_idle_1..8.png      格兰特（企鹅玩偶服）的待机动画（8 帧）
            │   ├── grant_emo_*.png          格兰特的对话立绘（开心/生气/惊讶/难过/害羞）
            │   └── laugh_idle_1..12.png     实验室里那只捧腹大笑的小家伙（12 帧，纯摆设）
            └── js/
                ├── main.js              入口：连服务器 → 画世界 → 开演
                ├── palette.js           ★ 全世界的配色与画法规范（取自实验室场景设计图）
                ├── sprites.js           每一样东西怎么画（实验台、玻璃柜、白板、窗户、配电箱……）
                ├── world.js             摆在哪儿、怎么动、这一关该看哪儿
                ├── chapters.js          七个碎片的剧本
                ├── ui.js                对话框、终端、面板、角色立绘
                └── api.js               和服务器说话的唯一通道
```

★ = 最值得先点开看看的文件

---

## 🧯 遇到了问题？

| 现象 | 原因与解法 |
| --- | --- |
| `ports in use` / 8080 被占用 | 有别的程序占着 8080。改 `application.properties` 里的 `server.port=8081`，同时把浏览器地址改成 `:8081` |
| `没找到 Java 21` | 按上面第一步安装，装完重开一个终端窗口 |
| 第 6 关改完配置没反应 | 配置只在启动时读一次：必须**先停掉服务器**（`Ctrl+C`）再重新 `./run.sh` |
| 页面一直转圈连不上 | 服务器还在启动（第一次要下载依赖），看一眼终端窗口的输出 |
| 想从头再玩一遍 | 游戏里通关后再进来会问你「继续逛 / 再来一次」，选后者即可。手动做也行：关掉服务器，删掉 `data/world.db`，再把 `config/world.properties` 里的 `stability` 改回 `0`，重新启动 |
| 页面白屏 | 按 `F12` 打开浏览器控制台，页面会把错误用大白话显示出来 |

---

## 🛠 技术栈

- **Java 21** + **Spring Boot 3**
- **Spring Data JPA**（ORM）+ **SQLite**（零安装的数据库）
- **PixiJS 8**（像素世界，无构建步骤，CDN 引入）
- 一个 HTML + 几个 ES Module，**不需要 npm / node**

没有 Docker，没有 K8s，没有微服务 —— 因为我们希望你第一次就真的看懂它。

---