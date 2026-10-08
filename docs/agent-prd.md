# Lodestar 项目研究 Agent PRD

当前基线：2026-10-08。本文统一产品逻辑；历史验收记录按日期保留。实现细节见 agent-architecture.md，操作见 agent-mvp-runbook.md、proactive-paper-discovery.md 与 paired-experiments.md。

## 目标与使用场景

用户询问最近的新技术，系统发现最新论文与平台热门论文，读取有界原文并解释。解释结合用户已有技术/方法接触记录及登记项目；用户选定相关论文后，系统给出有来源的适用性评估和具体技术方案，随后显式运行可复核的对照实验。项目订阅主动发现候选，用户仍控制深读、模型调用、实验执行与采用。

当前交付形式为本地 CLI 和 Agent 服务层；前端设计、Stitch/Figma 及正式前端实现暂缓。项目选择、查询词、目标问题和实验协议是输入；模型输出始终是待审核建议，不能作为事实或实验收益凭据。

## 两条入口、一个证据闭环

交互入口：用户消息 → 保守意图路由 → 新论文研究/证据追问/项目方案/学习反馈。研究工具 discover_papers 分 recent(arXiv) 和 trending(HF)，再 read_paper，assess/synthesis/novelty 形成讲解，保存研究 Trace、会话和证据。HF 热门仅是平台信号；正文范围不完整时明确指出摘要或片段限制。

订阅入口：登记并索引项目 → 配置可公开 query 与本地 term → watch tick/worker → 本地词法筛选 → 按项目与 arXiv 基础 ID 去重 → inbox。轮询需要进程运行，没有永久服务或推送通知。词法命中不等于语义适用。

选定候选后的顺序：watch read → evidence → assess → plan → experiment。handoff 可将已读证据交给持久化项目会话。阅读与发现不调用模型；评估/方案需要模型调用开关和精确项目文件外发范围。方案使用评估时的证据快照，论文读取或绑定代码片段变化后要求重评。实验不会因为方案生成成功而自动执行。

## 阶段、状态和可审阅产物

| 阶段 | 主要代码/命令 | 产物与判定 |
|---|---|---|
| 项目准备 | project add/index；memory/repo | 登记项目、本地有界文件索引；不是外发整个仓库的授权 |
| 候选发现 | agent/watch；watch tick/inbox | 来源、命中片段、渠道、去重 ID、运行错误；只产生候选 |
| 深读 | agent/candidate；watch read/evidence | read_id、正文/摘要范围、原文字符跨度、失败尝试；跨度不是 PDF 页码或语义证明 |
| 项目评估 | agent/applicability；watch assess | 方法、项目引用、迁移假设、缺口；assessed 与 contract_valid 只表示契约通过，semantic_review 仍 required |
| 方案草案 | agent/project_plan、plan_contract；watch plan/plans | read/assessment ID、证据快照、改动及实验协议；始终 draft；not_applicable 停止此路径 |
| 对照执行 | agent/candidate_experiment、eval/paired；watch experiment/experiments 或 ab | 冻结输入、两组实现、评分器、原始输出、哈希和 lineage；measured/pass/fail/inconclusive，采用状态独立 |
| 连续解释 | agent/conversation；chat | 持久化用户/项目绑定、证据与消息历史；复杂细节最多补读两篇已保存论文 |
| 记忆反馈 | memory/learning；learning/chat feedback | 技术与方法接触/自评/经核验表现事件；不是论文掌握标签 |

失败或缺口以状态返回并保留证据；不制造空成功。model_disabled、needs_export_scope、needs_assessment、needs_reassessment、needs_live_assessment 均不是已完成方案。mock/test 评估不能直接作为 live 方案依据。草案中的精确引文只能证明文本存在，不能代替迁移逻辑的审核。

## 记忆规则

研究缓存保存论文、研究笔记和可复用证据；用户学习事件保存 user、technology、method、paper URL、事件来源及表现证据。两者分开维护。掌握程度针对知识点/技术/方法，不针对论文；接触记录能回答读过哪些论文的哪些方法。

通用概念定义、组成、适用场景由 LLM 组织解释，不写成个人学习事实。系统生成讲解或用户说“懂了”不提升掌握。自动接触提取需要原文和已送达讲解两重引用校验。掌握依据要求调用方核验表现，撤销后按有效历史重算；自动判卷未实现。实验完成也不自动升级用户掌握。

## 模型与项目数据边界

远端项目内容限定指定 GitHub 仓库与用户已批准的三个精确文件片段：memory/learning.py、retrieval.py、agent/project_plan.py（均在 lodestar/ 下）。项目名称、描述及其他代码不进入此路径的远端上下文。片段数量和字符预算有界；不足时返回缺口，不能静默扩大授权。

默认模型调用关闭；只在当前账号/业务空间/模型免费额度与服务端用完即停确认后启用，不能付费回退。2026-10-08 已重新确认 qwen3.8-flash 剩余 845.54K 免费 Token、有效期至 2026-11-25、用完即停开启；用户已授权将之前提供的 API Key 保存到被 Git 忽略的本地 .env，真实 model-check 成功；新增两轮真实评估/方案链路已执行，均 uncertain；人工审核拒绝方案，语义质量验收未通过。密钥不进入代码和报告。

## 当前验收证据与未完成项

| 要求 | 当前证据 | 结论 |
|---|---|---|
| 真实近期/热门候选发现 | workspace/watch-validation-20261008/run.json、inbox.json；两渠道 ok、13 条候选 | 已验证公开检索与落库；合成项目，未验证语义相关性 |
| 真实候选正文和会话交接 | 同目录 candidate-read.json、candidate-handoff.json；12000 字符、8 跨度 | 已验证；不代表完整论文精读 |
| 当前评估与方案串接 | 单元测试、隔离 pipeline demo 的 assessment/plan | 夹具结构与状态通过；当前真实模型已调用；本轮两份方案人工审核拒绝，质量未通过 |
| 实际 A/B 记录 | workspace/agent-pipeline-demo-20261008/ab/result.json | 六个字段传递样本实际执行；不是论文方法收益实验 |
| 费用/外发保护 | 默认关闭、白名单、模式隔离与无调用测试 | 本地门禁已验证；当期额度已确认，本地凭据已配置、真实连接检查成功 |
| 用户记忆正确性 | 有效历史、撤销、自评、双重引用相关测试 | 已验证代码规则；自动表现判卷未实现 |
| 演示独立 | agent-demo、agent-pipeline-demo 隔离数据库与 manifest | 零 API 夹具演示可运行；历史回放不声称实时结果 |

剩余验收按顺序：免费额度与连接已确认；在批准文件范围内选择真实论文与具体项目问题，人工核对评估/方案引用与迁移假设；选择可落地的方法和独立标签，锁定依赖、两组实现和协议后实际比较；复核完整链路记录。未通过这些验收前，长期目标保持进行中。通知、永久后台服务、自动采用、自动掌握判卷和前端不属于当前已完成能力。


2026-10-08 证据版本修正：当前 URL 与阅读记录 URL 必须匹配，避免候选更新版本后静默复用旧版正文。旧尝试保留供审计，新版失败不能以旧版充当当前证据。无版本 URL 指向内容变化时仍需显式刷新；本次不宣称已检测这种变化。


最新真实运行与失败分析见 [候选方案审核](candidate-live-review-20261008.md)。正文和 live 链路已实际运行；不能因为契约校验通过而把被拒绝的技术方案算成已完成方法验收。


方案动作补充：propose_change 需要 1–3 项有据改动；investigate/no_change 需要明确理由及空改动列表。无需改动是合法研究结果，不能为了满足结构要求虚构项目缺陷。此类草案不能启动实现 A/B。新增真实论文 no_change 案例已经人工核对，见候选方案审核记录；真实方法迁移与收益仍未完成。
