# Agent 后端 MVP 验收记录

日期：2026-10-04。当前交付为本地 CLI 与服务层，可运行的 Agent 后端及隔离演示。免费模型验收使用 qwen3.8-flash；用户最新免费额度要求覆盖原计划 qwen-flash。前端未修改，实验方案不自动执行。

## 按目标核对

| 要求 | 已检查的证据 | 结论与范围 |
|---|---|---|
| 真实最新论文与热门发现 | workspace/mvp-acceptance-live/research.json、对应 tasks Trace：recent 20 条、trending 6 条，发现工具返回时间及状态 | 最近 arXiv 与 HF 平台热门分开；不代表全球最热或穷尽检索 |
| 有界正文、来源信息 | 同一记录 read_sources：InterEvolve 正文片段 12016 字符，Raven 摘要 1603 字符；字符跨度和覆盖标签、read_evidence.json；正文/检索测试 | 正文仅是片段，摘要降级明示，不宣称全篇覆盖 |
| 连续对话与持久化 | 同一会话关闭并重新打开 Workspace 后 followup.json 为 answered、复用 2 个来源；history.json；用户范围测试 | 已读证据可复用，失败时仍保存已读检查点；本地范围隔离不是服务认证 |
| 技术/方法接触记忆 | workspace/free-content-acceptance/learning.json 与 summary.json：真实模型记录 5 个 Harness 方法，含原文和已讲解文本、论文 URL，掌握全部 unknown | 独立于论文知识缓存；不存通用概念定义，不把阅读、自评当掌握 |
| 长期维护与撤销 | learning 仓储完整有效历史投影；旧掌握证据保留、近期展示有界、撤销重新计算的测试 | 不因近期事件多而丢失旧的有效掌握依据；自动判卷及别名归一未实现 |
| 有项目证据的结构化方案 | workspace/free-model-validation/plan-v3.json、plan-v3-review.json；只含获准三个代码文件片段和已读论文，原文精确匹配、片段 offsets | contract_valid=true，人工核对 grounding=passed；明确 draft/not_run，语义审查单独记录，不宣称实验收益 |
| CLI 启动说明 | agent-mvp-runbook.md、chat-usage.md；实际 chat send --help 和 agent-demo 命令 | 环境、禁用开关、免费调用条件及演示命令已整理 |
| 独立演示 | workspace/mvp-acceptance-offline-demo/manifest.json、workspace/mvp-acceptance-replay/manifest.json | 独立数据库或明确历史回放，model_calls=0，不改真实库；不同真实验证阶段由 provenance.json 标明 |

## 质量与验证限制

最终本地测试覆盖论文边界、失败状态、会话持久化、来源引用、方案约束、接触记忆、撤销、免费调用禁用及演示隔离。77 项 unittest 通过，git diff --check 通过。测试没有证明模型所有陈述均正确。

真实最后一轮研究的接触提取遇到顶层 JSON 数组，未写入方法。现已允许仅该阶段接收数组，并以测试验证其仍要求原文与已讲解文本双重匹配、不升级掌握。补充远端复验被自动审批拒绝（本地研究内容外发授权不足），未执行。因此该修复的远端重验未完成；方法记忆的真实证据来自此前独立的 5 条有效记录，而非最后研究会话。该验证缺口保留，不把不同阶段伪称同一次完全通过的运行。

模型仍可能从少量论文过度概括。窄范围 scope 检查为明显全域断言加待核对提示，不是事实判定器。检索相关性、正文覆盖、引用支持程度仍需用户审阅；词频和精确引文匹配不证明语义正确。方案的人工审查仅针对可追踪、可审阅的草案，不等于已采用或可复现收益。

较早 qwen-flash 验收中出现臆造依赖/指标的方案已拒绝，保留 qwen-flash-quality-review.md。免费模型第二版将词频包装为覆盖证明，也被拒绝；第三版改为来源定位与人工标注的待执行协议并通过依据核对。

## 后续阶段

真实 A/B 执行器及人工标注协议、主动定期项目关联检索、自动掌握评估、技术别名/语义召回和 UI 尚未实现。本期目标是基础 Agent 链路、技术方法接触记忆和有证据的实验草案，不能称为完整主动研究产品。


## 2026-10-08 本地复核

重新运行全部 77 项测试通过。实际 CLI 独立演示写入 workspace/mvp-offline-demo-20261008，复用 3 条证据，自评仍为 unknown；真实历史回放写入 workspace/mvp-replay-20261008。两者模型调用为零。此次没有向模型接口外发数据，未补齐上述远端重验，也未重新验证免费额度。

提交前检查未发现实际 Key 格式；.env、workspace 运行记录及数据库不进入提交。前端未变。该复核证明当前本地行为与演示可运行，不证明一轮实时完整端到端模型验收已全部通过。
