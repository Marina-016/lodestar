# 连续对话 CLI

## 当前交互入口（原生自主工具对话）

在仓库根目录运行：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m scripts.chat
# 或直接启用深入档位：
.\.venv\Scripts\python.exe -X utf8 -m scripts.chat --profile deep
```

出现 `你 >` 后输入问题或以下命令；不要在 PowerShell 的 `PS ...>` 后输入问题。

```text
/settings
/models
/models remote
/profile deep
/model <服务端模型ID>
/thinking on
/thinking on 4096
/thinking off
/thinking adaptive
/thinking effort high
/new
/exit
```

`/models` 展示本地映射；`/models remote` 按需查询 Anthropic/兼容网关目录，最多100条，
失败不会阻止手动切换。目录中的模型仍可能无调用权限，不能把目录当作调用成功。
模型切换沿用当前 provider、凭据和会话；不跨服务传输资料，也不改写 `.env`。
初始化失败保留旧配置；下一轮若服务不支持模型/思考参数，明确报错，不自动换模型或关思考。

| 档位 | 请求思考 | 输出上限 | 工具操作预算 |
| --- | --- | --- | --- |
| fast | 关闭 | 2048 | 4 |
| balanced | 关闭 | 8192 | 8 |
| deep | 开启；固定模式预算8192，或已配置effort/adaptive | 16384 | 12 |

这些是预算/思考预设，不是对模型价格或能力的评级。默认不强制选档，沿用环境配置，
标记为 custom。可用 `LODESTAR_MODEL_FAST/BALANCED/DEEP` 将档位绑定到自己服务的
模型ID；未配置映射就沿用当前模型，不假装换成更强模型。输出上限不是实际消耗，
深度思考与更多工具操作仍可能增加费用、延迟。`/new` 保留当前配置，仅重开对话。

启动参数也支持 `--model`、`--thinking on|off|effort|adaptive`、`--thinking-budget`、
`--max-tokens`、`--operations`、`--effort low|medium|high|max`。显式参数覆盖档位预设。
`/thinking on` 使用固定预算；新一代支持adaptive的Anthropic模型可改用`/thinking adaptive`。
预设尊重 `LODESTAR_LLM_THINKING_MODE`，不会靠模型名称猜测服务能力。

兼容格式不等于所有参数语义相同。例如DeepSeek官方兼容接口忽略`budget_tokens`，
用`output_config.effort`控制强度；可显式用`/thinking effort high`或`max`，此时不发固定预算。
参见[DeepSeek兼容字段说明](https://api-docs.deepseek.com/guides/anthropic_api/)及
[思考强度说明](https://api-docs.deepseek.com/guides/thinking_mode/)。当前内网网关是否完全
沿用此语义仍以请求/返回观察为准，不能声称预算值是服务端硬限制。

Anthropic 的固定思考显式发送 `thinking.type=enabled` 和 `budget_tokens`，与
`max_tokens` 分开配置，并保留回答空间；adaptive发送`thinking.type=adaptive`和effort，
不传固定预算。两者启用时不发可能冲突的temperature参数。参见
[官方思考配置文档](https://platform.claude.com/docs/en/build-with-claude/extended-thinking)。
DashScope发送`enable_thinking`和`thinking_budget`，并保留工具往返所需的reasoning_content，
参见[官方深度思考接口](https://www.alibabacloud.com/help/en/model-studio/deep-thinking)。
不会把原始思考文本当作回答展示或存入对话元数据；元数据只记录请求配置、返回模型、
是否观察到思考块及usage。观察到思考块不证明质量提高；adaptive也可能对简单问题不思考。
独立的结构化记忆提取仍使用非思考模式，避免与强制JSON工具冲突。

追问历史使用最近消息共享的24000字符预算，而不是每条只取前3000字符；优先保留
最近的完整分析。超出总预算时保留该条消息首尾并明确标记省略，避免结论部分无声丢失。

## 回答质量验收

普通聊天不走下面的历史报告模板。复杂问题侧重少量核心判断的机制、证据、取舍和
边界，按需读正文；不是固定要求读5篇，也不增加反思Agent或强制标题。简单问题仍直接回答。

```powershell
.\.venv\Scripts\python.exe -X utf8 -m scripts.eval_dialogue --live --quality --profile balanced
.\.venv\Scripts\python.exe -X utf8 -m scripts.eval_dialogue --live --quality --profile deep
```

同一模型/证据先比较harness，再单独比较思考或模型，避免混淆变量。人工检查：
结论是否有推导和依据、假设是否明示、能否比较替代方案、是否给出改变结论的条件、
实验能否区分假说；同时检查有无编造数字、过度归因。字数、标题数、非空回答和
`contract_valid`均不是质量通过标准。`--quality`使用虚构受控数据，不验证实时论文真实性；
不加`--live`仅验证离线管道。

## Headless CLI 与显式工作流

```powershell
.\.venv\Scripts\python.exe -m lodestar chat start --user default
.\.venv\Scripts\python.exe -m lodestar chat send --session SESSION_ID --intent research --message "Harness 有什么新进展" --mock --offline
.\.venv\Scripts\python.exe -m lodestar chat send --session SESSION_ID --message "这个方法和之前的区别是什么" --mock
.\.venv\Scripts\python.exe -m lodestar chat send --session SESSION_ID --intent feedback --technology Harness --method "Dependency-Scoped Propagation" --feedback self_report --message "我理解了大致思路"
.\.venv\Scripts\python.exe -m lodestar chat history --session SESSION_ID
```

真实模型调用去掉 --mock；--offline 只控制检索夹具。反馈、开始和历史不需要模型凭据。

开始时可指定 --project-id ID；后续 --intent plan 复用已有论文和登记项目生成草案。
默认 --intent auto 使用原生模型自主工具对话，可按需搜索、补读及核验；不再按关键词规则路由。
显式 --intent research/plan/feedback 保留各自工作流。研究证据持久化；旧回答不是来源证据，
没有重新检索就不声称更新。上述运行中切换命令仅在 scripts.chat 交互入口处理。

讨论、自评与尝试分别记录。普通追问不会静默提高掌握程度，用户的“懂了”也不是验证。当前没有自动评估复述正确性的能力。会话 user_id 隔离是本地数据边界，不是联网服务的认证系统。

离线会话夹具验证状态流转，不验证语言模型的追问质量。用户原始消息保存在会话中，讲解上下文使用最近的有界历史。

方法细节或方案每轮最多补读两篇已保存论文，使用隔离配置开启有界 PDF 正文读取。失败保留原证据，摘要降级不覆盖已有正文；补读记录写入 Trace。缺少明确技术上下文的自评先澄清，不写学习记忆。


初次研究和后续追问都会在讲解送达后尝试提取论文中的具体方法接触。方法必须同时匹配论文和本次讲解中的精确引用；没有支持项返回 no_supported_methods，提取失败返回 error 并保留回答。审计保存在回答元数据中，不增加系统对话消息；exposed/explained 不提升 mastery。追问附上实际提供的论文链接和阅读范围，清单不是逐句事实核验。


2026-10-08 追问讲解现在使用 conversation_grounded：论文陈述必须附指定来源的原文引用；只恢复 PDF 空白换行，不替换词语、标点或省略号。未匹配陈述不交付，原始模型草案和错误保留。每条通过的陈述附出处链接，推测、缺口及一般知识说明单独显示；字面校验不证明语义支持。一般知识仍由 LLM 组织，不写入个人学习记忆；方法接触只从通过校验的论文陈述提取，排除一般知识、推测、缺口。
