<div align="center">

# TraceSurface

**动态浏览器追踪 × JavaScript AST 分析。动静结合，从JS中还原完整的API。**

[快速开始](#快速开始) · [实战](#实战演示扫描站点登录页) · [AI Review](#ai-review) · [核心能力](#核心能力) · [工作原理](#工作原理) · [English](https://github.com/pis10/TraceSurface/blob/main/README.en.md)

<p>
  <img alt="Python 3.12+" src="https://img.shields.io/badge/Python-3.12+-3776AB?logo=python&logoColor=white">
  <a href="https://github.com/pis10/TraceSurface/blob/main/LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/License-MIT-22C55E"></a>
</p>

</div>

TraceSurface 面向渗透测试、安全评估与 API 资产盘点。从一个 URL 出发，它同时追踪浏览器运行时流量、解析 JavaScript AST。调用栈坐标将动静两端精确对齐，补全 baseURL，挖出页面从未触发的 API；无认证重放随后验证未授权与弱鉴权。

**核心原则：真实请求给出 runtime 证据，静态分析负责扩展；每条 API 都有证据，每层推导都有依据。**

## 实战演示：扫描站点登录页

目标是若依官方 Vue 登录页，不登录后台管理，从前端代码里挖出所有后台 API：

```bash
tracesurface scan https://vue.ruoyi.vip/login
```

![若依演示站登录页](https://raw.githubusercontent.com/pis10/TraceSurface/main/docs/images/ruoyi-login.jpg)

页面运行时只触发了 `GET /prod-api/captchaImage`，但这一条请求已经给出关键坐标。TraceSurface 将它精确对齐到 `app.js` 里的 `/captchaImage` 调用点，锁定 `/prod-api`，随后穿透 webpack 入口和懒加载 Chunk，继续还原登录后才会使用的后台 API：

![扫描若依演示站](https://raw.githubusercontent.com/pis10/TraceSurface/main/docs/images/scan-ruoyi.png)


| 指标 | 结果 |
| --- | --- |
| AST 调用点 | 316（去重前） |
| 还原出的 API | 135（runtime 1 · full-url 1 · L1 124 · L2 3 · L3 3 · L4 3） |
| 前端路由 | 发现 19，访问 19 |
| 重放请求 | 92（2xx 88，4xx 4） |
| 敏感信息命中 | 1 |
| 耗时 | 46.3s |

一个验证码请求，最终还原出 135 个 API。角色、菜单、部门、字典、监控、AI 会话——这些当前页面从未触发的后台接口，被一并挖了出来。

![报告界面：无认证重放结果](https://raw.githubusercontent.com/pis10/TraceSurface/main/docs/images/report-verification.jpg)

> [!NOTE]
> 88 条 2xx 里，有 79 条在响应体返回了 `code: 401`。

## 核心能力

- **真实浏览器追踪**：捕获 Fetch/XHR、发起调用栈、脚本、路由与微前端入口，拿到页面真实运行证据。
- **JavaScript AST 深挖**：识别 `fetch`、XHR、axios、自定义封装与网关调用，从代码中找回未触发接口。
- **懒加载穿透**：还原 webpack / Vite Chunk 映射，主动展开业务代码，不等用户逐页点击。
- **Stack-to-AST 对齐**：将 CDP 发起栈精确绑定源码调用点，标成 runtime，并还原 baseURL。
- **证据驱动推导**：按 runtime / full-url / L1–L4 / no-url 标注证据强度，每个结果都能回到依据。
- **无认证验证**：剥离 Cookie、Authorization 等认证信息重放请求，直击未授权与弱鉴权。
- **本地报告**：发现的 API、无认证重放结果与敏感信息集中呈现。
- **AI Review**：按站点分析重放结果，集中查看疑似未授权接口、判断依据与验证建议。

## 快速开始

从 [GitHub Releases](https://github.com/pis10/TraceSurface/releases) 下载对应平台的压缩包，解压后在文件所在目录运行：

```bash
# macOS / Linux
./tracesurface scan https://target.example

# Windows PowerShell
.\tracesurface.exe scan https://target.example
```

TraceSurface 优先使用本机 Google Chrome。未检测到 Chrome 时，首次扫描会自动下载 Chromium 到 `~/.tracesurface/browsers/`。启动本地报告：

```bash
./tracesurface serve
```

![启动本地报告](https://raw.githubusercontent.com/pis10/TraceSurface/main/docs/images/cli-serve.png)

浏览器打开 `http://127.0.0.1:8765`。

只想发现 API、不执行主动重放：

```bash
./tracesurface scan https://target.example --no-replay
```

<details>
<summary>从源码安装</summary>

要求 Python 3.12 和 [uv](https://docs.astral.sh/uv/)。

```bash
git clone https://github.com/pis10/TraceSurface.git
cd TraceSurface

uv sync
```

之后用 `uv run tracesurface ...` 运行。

</details>

## AI Review

扫描完成后，打开本地报告的 **AI Review**，点击 **选择站点分析**，再选择要分析的站点。
首次使用会先打开模型设置，保存后继续选择站点。

- 支持勾选站点，或分析筛选出的全部站点。
- 直接使用现有的 **2xx + JSON** 重放筛选，每个站点独立调用一次模型。
- 所有站点的疑似接口集中展示；点击可查看判断依据、验证建议和原始报文，不设风险等级。
- 扫描中的站点会跳过，完成后需手动发起。没有符合条件的响应时不调用模型。
- 关闭网页后后台任务继续；停止报告服务会中断未完成任务，可手动重试。

![AI Review：选择站点](docs/images/ai-review-sites.jpg)

模型设置提供两种方式：

- **从目录选择**：服务商与模型读取安装的 Pydantic AI 包，国内常用服务商优先排列。选择模型、填写 API Key 即可使用，不需要填写容量；调用参数由 SDK 和服务商处理。模型选择框也可输入尚未收录的型号。
- **自定义接入**：选择 OpenAI Chat Completions、OpenAI Responses、Anthropic Messages 或 Gemini generateContent 格式，填写模型名称、API 地址、密钥、上下文容量与最大输出。按强度设置思考时，先勾选支持的英文档位，再选择默认强度；也支持思考开关和 token 预算。能力由用户声明，不根据模型名称推测。

![自定义模型设置（本地模拟服务）](docs/images/ai-model-custom.jpg)

认证只需 API Key，无需选择认证方式或填写云凭据。目录中的默认服务地址由 Pydantic AI 处理，也可以更换地址。思考选项跟随 SDK 原生支持，强度直接显示英文参数值，不为单独厂商或型号添加补丁。SDK 尚未支持的参数可通过自定义接入填写；服务端是否接受参数及模型是否对账户开放，以实际调用为准。

点击 **测试连接**，使用当前配置验证连接及结果格式；测试只发送虚构样例，不包含扫描数据，也不会保存未提交的设置。请求和响应只在用户发起分析后发送至配置的服务。

密钥保存在本机数据目录的 `ai-settings.json`，不回传页面、不写入批次记录。切换服务商、API 格式或地址时，不复用旧密钥。自定义容量用于本地输入预算检查，不会改变服务端实际限制。

每条响应正文默认最多提交 **8 KiB**，超出部分截断并标记，不修改已保存的原始响应。输出会校验格式及接口编号，遗漏、重复、输出截断或调用失败均不会算作分析成功。整站输入超限时明确报错，不拆分或静默丢弃接口。

AI 给出的是人工复核线索。未展示的接口可能被判断为非疑似，也可能未分析、被跳过或分析失败；请结合任务详情查看。

> [!IMPORTANT]
> 本次为不兼容的数据及配置格式更新，不提供兼容或迁移。遇到旧数据目录时会停止并提示更换目录，不会自动改写旧数据。请通过 `TRACESURFACE_HOME` 使用新的目录。重新扫描同一站点仍会替换旧扫描及其关联的 AI 结果。

## 常见用法

| 场景 | 命令 |
| --- | --- |
| 扫描单个站点 | `tracesurface scan https://target.example` |
| 只发现、不重放 | `tracesurface scan https://target.example --no-replay` |
| 批量扫描 | `tracesurface scan -f targets.txt -s 10` |
| 打开浏览器手动触发页面 | `tracesurface scan https://target.example --headed --wait-ms 15000` |
| 保存登录态 | `tracesurface login https://sso.example.com` |
| 强制不加载登录态 | `tracesurface scan https://target.example --no-auth` |
| 启动报告 | `tracesurface serve` |

`login` 会把 Playwright `storage_state` 和可选的 `sessionStorage` 保存到 `~/.tracesurface/auth.json`，后续扫描默认自动加载。

运行 `tracesurface scan --help` 查看 `scan` 的完整参数。

## 如何解读未授权结果

TraceSurface 会重放两类请求：从前端源码推导出的 API 候选，以及浏览器捕获到的 Fetch/XHR 请求。

重放保留 URL、method、body 和 Content-Type，但不带浏览器里的 Cookie、Authorization 等认证头——拿到的就是未授权视角下的真实响应。默认只发送 GET 和 POST；其他方法需显式使用 `--allow-destructive`。

判断结果时记住一点：HTTP 2xx 不等于业务成功。不少接口对未授权请求也返回 200，只是在响应体里给 `code: 401`。结合响应内容和权限边界下结论。

## 报告视图

| 视图 | 用途 |
| --- | --- |
| **APIs** | 从前端还原出的接口清单，包含浏览器真实打过的请求 |
| **Replays** | 无认证重放的请求、响应、状态码与命中结果 |
| **Secrets** | 前端产物中的敏感信息命中与上下文 |
| **AI Review** | 跨站点汇总疑似接口，查看依据和原始报文 |

## 工作原理

TraceSurface 把运行时流量与静态调用点合成一条证据链：浏览器锁定真实 URL，AST 展开未触发接口，调用栈坐标负责把二者钉在一起。

### Stack-to-AST Alignment

每条浏览器请求都带着发起脚本、行号与列号；每个 AST 调用点也有对应的 source span。坐标一旦命中，真实请求与源码调用点就被确认为同一条 API 证据。

```mermaid
flowchart LR
    A["CDP<br/>真实请求 + 发起调用栈"] --> C["坐标对齐<br/>script URL · line · column"]
    B["tree-sitter<br/>API 调用点 + source span"] --> C
    C --> D["runtime<br/>运行时请求 ↔ 源码调用点"]
    D --> E["Evidence-driven<br/>API Surface"]
```

1. CDP 记录 Fetch/XHR 的脚本、行号和列号。
2. tree-sitter 提取 API 调用点及源码位置。
3. 坐标命中后，请求标成 runtime，并可为其他调用点提供 baseURL。

### 证据层级

每条 API 落在一条证据轴上。runtime 是浏览器实际打到的请求；其余按 URL 从哪里来、把握有多强分层。

| 层级 | 含义 |
| --- | --- |
| **runtime** | 浏览器真实打过这条请求 |
| **full-url** | 源码里已经是完整 URL，不用拼 |
| **L1** | 路径能唯一绑到一个已知 base |
| **L2** | 通过 client 关系或扇出绑定 |
| **L3** | 使用本轮扫到的全局 base |
| **L4** | 仅用目标站点 origin 兜底 |
| **no-url** | 找到了调用点，但还拼不出完整 URL |

## 数据目录

数据默认保存在 `~/.tracesurface/`，可以通过 `TRACESURFACE_HOME` 指定其他目录。

```text
~/.tracesurface/
├── tracesurface.db
├── responses/
├── sources/
├── logs/
├── browsers/       # 备用 Chromium（按需）
└── auth.json
```

## 技术栈

- Python、Typer、asyncio、httpx
- Playwright、Chrome DevTools Protocol
- tree-sitter、tree-sitter-javascript
- SQLite、FastAPI、Uvicorn
- React、TypeScript、Vite、Tailwind CSS

## 安全与授权

TraceSurface 只应用于你拥有或已获得明确授权的目标。扫描默认会主动发送 GET 和 POST；`--allow-destructive` 会放行其他方法。不需要验证时请使用 `--no-replay`。

## License

[MIT](./LICENSE)
