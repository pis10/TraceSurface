# AI Review 产品与接入设计

## 使用路径

用户主动发起分析，以站点选择范围，每个已完成站点独立调用一次模型。复用已有 `2xx && JSON` 重放筛选；零符合条件的站点跳过。未完成扫描不会等待后自动分析。主列表汇总疑似接口，不设风险分层，也不生成一站一份报告。

## 模型设置

通过包依赖引入 `pydantic-ai-slim[openai,anthropic,google]`，不复制 SDK 源码，不部署网关、不启用工具或历史对话。

- 内置服务商：读取 SDK 的已知模型与原生 Provider，国内常用服务商优先排列。仅包含已安装消息 API 支持、使用 API Key 的在线服务。模型选择支持搜索和输入新型号。**不展示或要求填写容量**，不因 SDK 缺少元信息而让用户补全；原生调用不设置自定义的 max_tokens，由 SDK / 服务商负责默认限制。
- 自定义接入：四种消息格式为 OpenAI Chat Completions、OpenAI Responses、Anthropic Messages、Gemini generateContent。用户填写地址、模型名称、API Key、上下文容量、最大输出及结果格式。名称仅为服务端路由标识，不据此继承官方模型 Profile。
- 自定义思考强度：提供 `minimal / low / medium / high / xhigh / max` 复选项，用户勾选服务实际支持的档位，然后选择默认强度。未勾选的档位不能成为默认值；取消当前默认档位时清空默认值，保存前要求重新选择。保留顺序与选择，所有档位只用英文。开关、预算、自适应为独立方式，不把它们混入强度档位。
- API 格式决定参数结构，用户声明决定具体能力。Chat Completions 的强度使用 reasoning_effort，开关 / 预算使用 thinking 扩展；Responses 使用 reasoning.effort；Messages 使用原生 effort / thinking；Gemini 使用 thinking_level / thinking_budget。均通过 Pydantic AI 的公开原生设置传递，不写厂商或型号特例。
- 内置模型的思考开关依据公开 Profile，具体强度仅展示 Profile 明确描述的枚举。SDK 未提供完整信息时保留默认，不从通用类型猜模型支持的所有档位。Kimi 在当前 SDK 中不能通过统一开关关闭思考，因此内置配置不展示关闭选项。
- API Key 只存本机（0600），不返回页面或写入批次。切换接入模式、服务商、API 格式或地址不复用旧密钥。没有认证方式选择和环境变量密钥回退。

## 调用与结果

单次 `Agent.run`，无工具、无历史、输出重试为 0、模型请求限制为 1，底层 SDK HTTP 重试同样关闭。Responses 禁用服务端存储。

JSON Schema 使用 `NativeOutput`；JSON 模式使用 `PromptedOutput`；普通文本使用带 Schema 提示词的 `TextOutput` 解析函数。三种模式最终都得到同一个严格 Pydantic `SiteResult`。再校验接口编号完整性，遗漏、重复、未知 ID、错误类型及输出截断都不能记为成功。自定义输入通过本地 token 估算检查声明的上下文，预留用户声明的最大输出；这是估算，不是厂商精确 tokenizer。

连接测试与正式分析共用全部调用、能力和结果校验，使用虚构报文，不发送扫描数据、不保存表单。错误映射为用户可读提示，不暴露服务端原文、请求报文或密钥。

## 数据与验证

配置格式 4，不兼容或迁移旧配置；数据库结构本轮不变。站点调度、三并发、失败隔离、批次快照和疑似结果存储继续由 TraceSurface 管理。

测试覆盖四种真实 SDK 请求到本地模拟 HTTP 服务、自定义强度的原值传递、声明与默认值约束、容量仅自定义生效、原生模型名称不污染自定义 Profile、完整结果校验、零接口不请求、无自动重试、凭证隔离、配置 API 与站点任务流程。测试使用临时数据和虚构 Key；不进行本机原生二进制编译，不推送。

参考：[模型与服务商](https://pydantic.dev/docs/ai/models/overview/) · [输出机制](https://pydantic.dev/docs/ai/core-concepts/output/) · [安装](https://pydantic.dev/docs/ai/overview/install/)
