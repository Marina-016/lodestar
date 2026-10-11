# 官方 WebSearch MCP 接入

Lodestar 保留原来的 `search_web(query, max_results)` 工具接口，新增可选后端 `aliyun_mcp`。模型自行决定是否搜索；论文检索与已知网页读取继续使用各自工具。适配没有增加意图路由或固定工作流。

连接阿里官方 Streamable HTTP 地址 `https://dashscope.aliyuncs.com/api/v1/mcps/WebSearch/mcp`，使用官方 MCP Python SDK 和本地 `DASHSCOPE_API_KEY`。先 initialize / tools/list，再按服务端公布的 schema 调用唯一的 query 搜索工具。2026-10-10 官方工具页显示 `bailian_web_search(query: string, count?: integer)`。只发送搜索词和数量，不发送整个聊天或项目代码。未知输出保留为 provider_text，不虚构来源卡片。

## 当前状态 · 2026-10-10

SDK 已安装，适配和离线测试已完成。控制台登录过期，账号服务未开通的诊断尚未解除；剩余免费次数未核实。**尚未切换本机运行后端、发放本地调用额度或执行真实 tools/call，搜索恢复尚未验收。**

## 开启步骤

1. 在百炼 MCP 广场登录并开通「联网搜索」。开通涉及服务协议时由用户确认。
2. 在已登录账号核实实际剩余免费次数、有效期及同账号其他客户端的用量，不能把宣传的 2000 次当作剩余额度。需要云端免费用完即停时应在控制台核实搜索服务自身支持；模型的免费额度设置不代表 MCP 也被保护。
3. 安装 `pip install -e ".[web-search]"`。配置 `LODESTAR_WEB_SEARCH_BACKEND=aliyun_mcp`，复用 `.env` 中的 `DASHSCOPE_API_KEY`。官方外部调用要求有效的百炼通用 API Key；业务空间专用 Key 是否适用须实际验证，不自动创建或替换凭据。
4. 操作者核对后通过 `lodestar.providers.search_quota.grant()` 建立被 Git 忽略的 `workspace/search/free-quota.db` 额度记录。参数为已核实剩余次数 `verified_remaining`、不大于它的本轮上限 `call_limit`、核对依据 `evidence` 与有效期 `valid_for_s`（默认一小时）。密钥参数从本地环境读取。模型没有发放额度的工具。
5. 先执行少量直接搜索验证来源与返回结构，再恢复用户对话。重启服务前等在途 run 完成。

## 调用约束及实际边界

没有记录、记录过期、凭据变更、额度耗尽或记录无法读取时，不连接搜索服务。每次网络尝试前持久化扣减一次；并发请求通过 SQLite 原子扣减，服务重启不会重置。失败或超时不退回次数，不自动重试，不回退到其他收费搜索。一次尝试最多一次 tools/call。inspect 模式只 initialize / tools/list，不调用搜索。

本地计数只覆盖共用该记录的 Lodestar 实例，**不是阿里云账号总账或云端禁止付费开关**。同账号其他程序可能消耗共享免费额度，因此并行使用其他客户端、用量不明或免费保护无法确认时，应停止并重新核对，不能承诺只靠本地计数避免所有云端计费。

不会启用模型内置的 `enable_search`。它与此 MCP 服务独立计费，且不提供免费搜索调用额度。

## 验证

292 项全量离线回归通过。仅 initialize / tools/list 的 SDK 连接验收仍返回服务不可用，没有执行 tools/call。新增离线测试覆盖缺失/过期/耗尽/账号变更、20 次并发尝试最多获准 3 次、失败不退款不重试、SDK 工具发现与 query/count 参数、来源 provenance、未知文本保留和未确认额度时无网络请求。离线夹具不构成真实服务质量验收。

官方依据：[外部调用](https://help.aliyun.com/zh/model-studio/mcp-external-calls/)、[MCP 计费](https://help.aliyun.com/zh/model-studio/mcp-introduction)、[联网搜索计费区分](https://help.aliyun.com/zh/model-studio/web-search/)。官方说明为前 2000 次免费，用完按 29 元/千次计费；因此实际使用前必须核实账号余额度。
