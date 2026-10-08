# 项目关联的主动论文发现

实现日期：2026-10-08。本阶段将主动检索、项目订阅、候选收件箱与后续论文讲解分开。只有显式配置的公开 query 发往 arXiv/HF，不从仓库代码或项目描述自动提取外发内容。匹配 term 在本地使用；整个 watch 链路不创建 LLM 客户端。

## 阶段与边界

1. 对已登记项目添加订阅，指定公开查询、匹配词和间隔。只有 active 项目会被轮询。
2. tick 取到期订阅，并用数据库租约避免多个 worker 同时处理。租约过期可恢复，订阅和下一次运行时间跨进程保存。
3. 检索最近 arXiv 论文与 HF 平台热门，各最多 20 条；recent 时间窗口按间隔留一天重叠、最多 30 天。HF 热门不是全网最热，旧论文也可能热门。
4. 本地在标题/摘要片段匹配技术词或短语，保留字段、命中词及原文片段。这只是可解释的候选筛选，不是模型判断的项目适用性。
5. 同项目以 arXiv 基础 ID 去重，合并发现渠道，更新 last_seen，重复运行不重复增加候选。离线夹具有独立身份前缀及 discovery_mode，不覆盖真实来源。
6. 保存运行状态和来源覆盖，成功后按间隔继续；单渠道失败标 partial，两者失败标 error，最迟一小时后可重试。不把失败伪装成无新论文。
7. 候选不自动深读、生成方案、改代码或写入用户掌握记忆。用户选定论文后使用现有对话研究及项目方案路径继续。

## CLI

先用 `lodestar project list` 获取已有项目 ID；新增 GitHub 项目可运行 `lodestar project add URL --status active`。新增命令可能获取 GitHub 内容，但 watch 不上传其索引。

```powershell
# PROJECT_ID 替换为实际项目 ID；query 应只包含可以公开发送的词。
.\.venv\Scripts\python.exe -X utf8 -m lodestar watch add --project-id PROJECT_ID --query "agent harness" --term harness --term "tool use" --interval-hours 24
.\.venv\Scripts\python.exe -X utf8 -m lodestar watch tick
.\.venv\Scripts\python.exe -X utf8 -m lodestar watch inbox --project-id PROJECT_ID
# 持续轮询到期订阅；进程需要保持运行，Ctrl+C 停止。
.\.venv\Scripts\python.exe -X utf8 -m lodestar watch worker --poll-seconds 60
.\.venv\Scripts\python.exe -X utf8 -m lodestar watch disable --watch-id WATCH_ID
```

worker 只是应用运行进程，未替用户创建系统开机任务。可由现有进程管理或 Windows 任务计划程序定时执行 tick；本次没有修改系统任务。--cycles N 用于有限运行验收，0 表示持续运行。运行器关闭期间没有主动抓取；重新开启后处理到期订阅，超过 30 天的缺口无法保证补齐。

## 隔离验收与实际结果

离线测试需配置单独 LODESTAR_DB_PATH / LODESTAR_WORKSPACE_DIR，使用 `watch tick --offline` 或 `watch worker --offline --cycles 2 --poll-seconds 10`。不要把夹具当作实时推荐。

2026-10-08 在独立数据库执行真实公开查询 agent harness，arXiv 与 HF 两者返回 ok，形成 13 条去重候选，模型调用为零。记录：workspace/watch-validation-20261008/run.json、inbox.json。订阅使用合成项目，没有上传实际项目内容；这一验收证明真实发现和落库，不证明论文对 Lodestar 语义适用。

测试覆盖跨版本去重、合并渠道、重启到期判断、暂停/禁用、租约恢复、失败/部分成功、查询外发边界、自评记忆不被修改、离线来源与真实来源隔离。

## 下一步

在获准上下文内补充语义适用性评估和具体代码依据，串起候选深读与方案草案；再考虑用户可控通知、标记已处理及可复现 A/B。暂未自动生成每日方案或发送通知，也未启用永久后台服务。

本轮最终全量 unittest 为 85 项，通过。实际 CLI add -> worker 两轮 -> inbox -> disable 通过，第二轮没有重复执行尚未到期订阅；记录在 workspace/watch-cli-validation-20261008/result.json。git diff --check 通过。


## 候选深读与项目会话交接

```powershell
# RECOMMENDATION_ID 来自 inbox。全文模式需要显式打开；不调用模型。
$env:LODESTAR_FULL_TEXT = "true"
.\.venv\Scripts\python.exe -X utf8 -m lodestar watch read --project-id PROJECT_ID --recommendation-id RECOMMENDATION_ID
.\.venv\Scripts\python.exe -X utf8 -m lodestar watch evidence --project-id PROJECT_ID --recommendation-id RECOMMENDATION_ID
.\.venv\Scripts\python.exe -X utf8 -m lodestar watch handoff --project-id PROJECT_ID --recommendation-id RECOMMENDATION_ID --user me
# handoff 返回 conversation_id；后续 chat followup/plan 使用现有模型授权与调用开关。
```

阅读保存在独立的 paper_candidate_reads 历史中。重复读取复用缓存；--refresh 追加新尝试，不删除旧证据。失败仍有审计记录，不能创建空证据会话；正文刷新退回摘要时保留已有正文供复用，同时新尝试如实标明摘要。证据带来源、读取层级、覆盖、跨度和模型未评估状态，跨进程保留。这里只交接论文证据与项目 ID，不外发项目代码，也不自动认定适用或掌握。

离线推荐必须配 --offline 阅读，真实推荐禁止用离线夹具覆盖。选择已有正文优于较新的摘要；如果后续论文版本变化，需要显式按版本管理和重读，此阶段不声称证据一直最新。


## 项目适用性评估

`watch assess --project-id PROJECT_ID --recommendation-id RECOMMENDATION_ID --goal "要核对的项目问题"` 生成独立评估；可加 --mock 验证状态流转。先成功 read，且项目有相关本地索引。输出论文方法、项目依据、迁移假设、limitations 和精确引文；即使 contract_valid，semantic_review 仍 required，不能自动实施方案。每次评估绑定具体 read_id 并保存，不修改掌握记忆。

live 项目上下文须同时匹配 LODESTAR_PROJECT_MODEL_ALLOWED_REPOSITORY 和 LODESTAR_PROJECT_MODEL_ALLOWED_PATHS（逗号分隔的精确索引路径，无通配符）。缺少授权范围返回 needs_export_scope，默认调用关闭返回 model_disabled；这两种情况不会创建模型客户端。只从批准路径取最多三个 4000 字符片段；不外发项目名称/描述或其他代码。此配置记录已有人工授权，不代表可以自行扩大范围。

适用性评估暂未进行新一轮真实模型调用。已验证真实候选正文读取与会话交接，记录在 workspace/watch-validation-20261008/candidate-read.json、candidate-handoff.json。语义适用性及整条链路的真实模型验收仍待后续推进。

本轮扩展后最终 100 项 unittest 通过，CLI mock 评估/未授权 live 阻止验证见 workspace/watch-validation-20261008/candidate-cli-assessment.json。旧式模型项目关联会外发名称/描述/技术栈，现已在 live 模式跳过，避免绕过精确文件白名单；离线演示保持原流程。演示索引采用已声明的核心路径优先，防止文档增长挤掉必要代码证据，仍遵守文件预算与根目录边界。


## 候选到方案与实验（2026-10-08）

新增 watch plan/plans/experiment/experiments。方案复用选定适用性评估的论文与项目证据快照，不因新 goal 静默换一组证据。正文重读或已绑定项目片段改变后返回 needs_reassessment。默认选择最近成功评估，失败尝试不覆盖成功记录；not_applicable 不继续生成方案，mock/test 评估不能直接进入 live 方案。所有草案保留 semantic_review required。

成对实验保存方案血缘及实际输出，详见 [paired-experiments.md](paired-experiments.md)。112 项全量 unittest 通过，隔离 agent-pipeline-demo CLI 完成，模型调用与掌握事件均为零。真实发现/阅读已验证；新增评估与方案仍只有本地夹具验收，百炼当前已登录，免费额度和用完即停已重新确认；本地凭据已获准配置，真实 model-check 成功；语义评估尚未验收。历史测试数量是当时阶段记录。


2026-10-08 证据版本修正：当前 URL 与阅读记录 URL 必须匹配，避免候选更新版本后静默复用旧版正文。旧尝试保留供审计，新版失败不能以旧版充当当前证据。无版本 URL 指向内容变化时仍需显式刷新；本次不宣称已检测这种变化。


版本更新规则：同一 arXiv 基础 ID 仍只占一条候选；发现更高明确版本时更新来源并恢复 unread，旧阅读记录保留，但不能作为新版阅读缓存。渠道合并时优先最高明确版本，较旧明确版本或无版本 URL 不覆盖已知较高版本。无版本 URL 的内容变化不能靠此规则检测，需要显式刷新。
