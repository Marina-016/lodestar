# Lodestar 项目研究 Agent PRD

当前基线：2026-10-08。本文定义当前产品逻辑与交付边界；历史试验保留在各自验收记录中，不覆盖失败结果。实现见 [系统架构](agent-architecture.md)，操作见 [运行手册](agent-mvp-runbook.md)、[项目订阅](proactive-paper-discovery.md)、[成对实验](paired-experiments.md)。

## 产品目标与当前交付

用户问最近有什么新技术，Agent 以论文为主要来源，发现近期论文和平台热门论文，读取有界原文，结合用户技术/方法接触记录进行解释。登记项目后，可以订阅相关论文、逐篇评估迁移依据，形成技术方案或调查计划，并保存实际实验结果。当前交付是本地 CLI 与 Agent 服务层，以及独立演示；UI、Stitch/Figma 和正式前端暂缓。

“最新”对应 arXiv 的滚动发布时间窗口；“热门”对应 HF daily papers 的平台信号，不代表全网热度。项目查询词和技术主题由调用者提供；本地词法匹配只生成候选。模型输出、引文通过、测试通过和用户说“懂了”分别不能替代研究结论、方法收益或用户掌握证据。

## 两条入口

交互：用户消息 → 保守意图路由 → research / followup / plan / feedback。research 调用发现与阅读工具，再生成证据评估、跨论文讲解及研究笔记；followup 复用会话证据，细节问题最多补读两篇已保存论文；plan 使用登记项目的有界证据；feedback 记录用户接触或自评。会话持久化用户、项目、消息和已读证据，重启可恢复。

主动发现：登记并索引项目 → 配置公开 query 和本地 term → watch tick/worker → 本地筛选 → inbox。发现和阅读不调用模型。到期轮询有租约、错误与重试记录；需要运行进程，尚未安装永久后台服务或推送通知。

候选研究：read → evidence / handoff → assess → plan → 按动作分支。handoff 将已读证据交给绑定项目的会话。评估和方案需要显式开启模型调用，并满足精确项目文件外发授权。方案复用评估的阅读与项目证据快照，证据变化要求重新评估。

## 阶段、调用与产物

| 阶段 | 实际组件或 CLI | 产物与判定 |
|---|---|---|
| 项目准备 | project add/index；memory/repo | 本地登记及有界索引；不是外发整个仓库的授权 |
| 发现 | discover_papers；watch tick/inbox | recent / trending 来源、日期、命中片段、渠道和错误；按项目/arXiv 基础 ID 去重 |
| 阅读 | read_paper；watch read/evidence | 正文片段或摘要、read_id、coverage、原文字符跨度与失败记录；跨度不是 PDF 页码 |
| 适用性 | agent/applicability；watch assess | 论文方法、项目引用、迁移假设、反证/缺口；relevant / uncertain / not_applicable；契约通过后仍需语义审核 |
| 技术草案 | project_plan + plan_contract；watch plan/plans | read/assessment ID、证据快照、action、理由、实验协议和风险；始终 draft |
| 实现型实验 | candidate_experiment + eval/paired；watch experiment/experiments、ab | 固定输入、两组实现、评分器、日志、原始输出、哈希与关联；measured/pass/fail/inconclusive |
| 调查型实验 | eval/context_policy + context_audit；context-policy-experiment/check | 独立受限上下文策略比较，保存协议、原始材料、逐轮输入/响应、修订及评分 |
| 调查登记 | watch investigation/investigations | 有效 investigate 草案与同论文诊断记录绑定；保存结果快照、哈希和完整关联，零模型调用 |
| 讲解与接触 | ConversationAgent；chat；agent/exposure | 初次研究和追问后进行原文/已送达讲解双重引用校验；追问提取失败保留回答和审计状态 |
| 学习反馈 | memory/learning；learning、chat feedback | 按技术/方法保存接触、自评、经调用方核验的表现证据；撤销后重新计算 |

## 方案动作与失败状态

propose_change：需要 1–3 项有依据的改动，可在审核实现和固定协议后显式运行成对实验。investigate：需要明确调查理由，改动列表为空，可以运行独立诊断并登记结果。no_change：需要有据理由，改动列表为空，停止改动路径。不能为了生成改动而虚构缺陷；调查或无需改动草案不能启动实现型 A/B。

model_disabled、needs_export_scope、needs_assessment、needs_reassessment、needs_live_assessment 表示门禁或缺口，不是成功方案。mock/test 评估不能直接成为 live 方案依据。候选 URL 与阅读记录 URL 必须一致；版本更新不能复用旧版正文，新版读取失败也不能用旧版充当当前证据。无版本 URL 的内容变化仍需要显式刷新。

引用校验只证明指定文本存在，不证明迁移合理。模型或工具失败保留错误和已有证据；综合失败后可复用 read_evidence.json。实验执行、实验判定、人工语义审核和采用状态独立，均不自动提升用户掌握。

## 两种记忆的维护

研究知识库存论文、研究笔记和可复用来源证据；学习事件库存 user、technology、method、paper URL、事件来源与表现证据。掌握针对知识点、技术或方法，不针对论文；论文关系用于回答接触过哪些文章中的哪些方法。

概念定义、组成、适用场景由 LLM 组织，不写成个人学习事实。讲解只产生 explained 接触事件，且必须同时找到原文和本次讲解中的引用。懂了是 self_report；示范类掌握依据要求调用方核验，自动表现判卷未实现。撤销后根据完整有效历史重新计算。追问的提取审计附在回答元数据中，不作为新对话消息送回模型。上下文实验只修改临时研究状态，不修改来源记录或用户学习库。

## 模型、费用与项目数据

默认 LODESTAR_MODEL_CALLS_DISABLED=true。真实验证只使用已核对免费额度且服务端用完即停的阿里 qwen3.8-flash，不付费回退。额度是账号/业务空间/模型的当期外部状态，本地开关或 --free-quota-confirmed 不检测云端计费。2026-10-08 最近一次控制台核对为 772.77K，期限至 2026-11-25；随后实验继续消耗额度，这不是当前余额承诺。API Key 仅保存在 Git 忽略且未跟踪的 .env，不写入报告。

远端项目内容只允许指定 Lodestar 仓库的 lodestar/memory/learning.py、lodestar/retrieval.py、lodestar/agent/project_plan.py 有界片段。项目名称、描述、其他代码不进入该路径的远端上下文。缺少依据返回缺口，不能扩大授权。公开论文与专门的合成诊断材料可用于独立验证。

## 当前验证与待验收边界

| 要求 | 当前证据 | 能证明的范围 |
|---|---|---|
| 真实发现/阅读/交接 | watch-validation-20261008：13 个候选、两渠道、12000 字符正文、8 跨度 | 公开检索、落库、有界阅读与交接；不是项目语义适用性 |
| 真实评估/方案 | candidate-live-review-20261008.md；Raven no_change、CLM investigate | 已调用并人工检查部分结果；早期臆造改动已拒绝并保留，不能声称模型普遍可靠 |
| 实际实现型 A/B | final-pipeline-audit-20261008；六个字段传递样本，final-pipeline-environment-20261008 包含环境快照 | 实际冻结代码执行与评分；不是论文方法收益 |
| 实际论文方法调查 | context-policy-investigation.md；两个已知合成案例 | 自由编辑联合正确率 1/2 对 1/2；来源绑定修正后 1/2 对 2/2；不是独立测试集、完整 CLM 复现或总体收益 |
| 调查结果关联 | candidate-context-validation-20261008；investigation #1 | 同论文/项目/阅读/评估/方案关联；执行后本地登记，不是预注册或认证执行 |
| 独立演示 | final-agent-audit-20261008、final-pipeline-audit-20261008 | 重新运行、独立库、零模型调用，标记 offline_fixture |
| 用户记忆规则 | 138 项回归，包括追问支持引用、失败保留和用户隔离 | 实现规则通过；新增追问接触的真实模型验收仍待完成 |

综合验收仍进行中：新运行已保存环境版本和复现边界；验证新增追问接触的真实模型输出，逐项汇总证据。不以两案例结果自动采用上下文策略，不将调查原型接入生产来制造“已落地”结论。永久服务/通知、自动采用、自动掌握判卷、语义召回与前端属于后续扩展，不属于当前已完成能力。
