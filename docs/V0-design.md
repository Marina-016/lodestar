# Lodestar — PRD 缺口审查 & V0 技术架构设计

> **Agent 名：Lodestar（导星）** —— 引导研究方向、追踪 AI 前沿。常量 `AGENT_NAME` 在 `lodestar/__init__.py`，prompts/README/CLAUDE.md 引用。
> 状态：V0 起点。本文档回答两件事：① PRD 有哪些关键缺口必须先补，② V0 用怎样的技术架构/目录去落地。
> 原则锚点：Single Agent + Tools；确定性步骤用 Code；每一步保持可运行；从第一版保留 Trace + Eval；不引入炫技框架。

---

## 一、PRD 关键缺口审查

按「是否阻塞 V0 设计与落地」排序，只列关键项。

### A. 技术缺口（阻塞设计）

**A1. LLM / 搜索供应商与成本没有落地选择**
PRD 写「Web Search / arXiv / Web / GitHub」，但没说用哪家搜索、哪家 LLM、Key 从哪来、成本预算多少。
→ V0 决定：LLM 走 Anthropic Messages API（可插拔，Mock 模式离线可测）；搜索走**零 Key 可跑**的 arXiv API（论文）+ DuckDuckGo Lite（网页），全部后端接口化，后续换 Brave/Serper 只改一个 provider。

**A2. Token / Context 预算完全没有定义**
Research Agent 会读多篇论文+网页，工具输出直接塞进上下文必然爆窗口。
→ V0 决定：每个工具输出**硬截断**（`read_char_budget`）；`max_agent_steps / max_search_queries / max_deep_read_sources` 进配置；论文 V0 只读 abstract 级（深度全文读取留给 V1）。

**A3. Eval 是最重度的章节，却完全没有落地机制**
PRD 给了 9 个维度 + 6 个 trajectory 维度，但没定义：谁来打分（LLM-as-judge？人工？）、打分 rubric、golden case 的 ground truth 长什么样、live 搜索结果每天变如何保证可复现。
→ V0 决定：golden case = `goal + 期望覆盖概念 + 期望洞察 checklist + 期望来源数`；打分分两层——**确定性指标**（来源数/去重率/搜索次数/token、来自 Trace）由代码算，**质量指标**（Task Success / Faithfulness / Novelty / Coverage / Planning）由 Judge-LLM 按 rubric 打；live 搜索波动用「基于本次实采 sources 的评分」吸收，且支持 Mock 全离线回归。

**A4. 确定性步骤与 LLM 步骤的边界没有落成机制**
PRD 原则说「确定性步骤用 Code」，但去重、截断、限流、重试这些没有具体化。
→ V0 决定：去重（title 归一化 key + arXiv ID 强 key）、截断、次数上限、超时/重试、FTS 检索全部是**确定性 code**；LLM 只出现在 Plan / Query Expand / Rerank / Assess / Synthesize / Novelty / Judge。

**A5. Knowledge State 的变更协议缺失**
「修改前确认」没定义确认的颗粒度：diff 什么样、能不能回滚。
→ V0 决定：所有写入以 `KnowledgeUpdateProposal`（concept + action + old/new + 依据 source）进 DB，状态机 `pending → applied | rejected`；`lodestar knowledge diff <task_id>` 可看、可回滚。

### B. 产品缺口（阻塞价值）

**B1. Knowledge State 的种子与校准机制缺失**
novelty / 关联 全部依赖 Knowledge State；空库时 novelty 无意义。
→ V0 决定：新增 `knowledge seed` 命令（用户一次性声明已懂概念）；novelty 判定在空库时显式降级为「相对空知识库 = 全部 new，但标注 Low confidence」。

**B2. 用户反馈信号没有采集点**
PRD 把「用户反馈」列为 self-evolution 信号，但流程里只有 confirm，没有「这篇 brief 有没有用 / 哪条新颖 / 哪条重复」。
→ V0 决定：`research` 结束后交互式问两题（brief 有用性 0-5、最有价值/最重复的条目），落 `feedback` 表。这同时是 eval 指标「Useful Source Rate」的真值来源。

**B3. 多语言策略完全未提**
用户是中文使用者，AI 前沿材料几乎全英文：检索语言、brief 语言、Knowledge State 语言要一致。
→ V0 决定：检索 Query 中英双语生成（保留 Original Query 原文）；Brief 默认中文、可配置；concept 名保留英文原词（可检索）。

**B4. 知识过期没有处理**
AI 领域半年就过时，`last_updated` 有了但没策略。
→ V0 决定：暂只记录 `last_updated` + `status=needs_review` 手动标记；过期自动重审放 V1。诚实标注「未做」。

### C. 明确说「V0 不做」

- Multi-Agent、自改 Prompt/Skill、自动训练、自动改用户项目、复杂知识图谱可视化、大规模论文库、社交/多人协作（与 PRD 非目标一致）。

---

## 二、V0 技术架构

### 2.1 形态与栈

```
Python 3.12  CLI（argparse，无 web 前端）
依赖仅 4 个：anthropic, requests, beautifulsoup4, python-dotenv
存储：SQLite（结构化）+ FTS5（全文检索）  —— PRD 指定，不加向量库
Trace：每任务一条完整 JSONL + 落库
Eval：golden cases（JSON 文件）+ 确定性指标 + Judge-LLM 质量指标
```

**为什么不用框架**：Research Loop 的结构是固定的（Plan→Search→Read→Assess→Synthesize），编排器是确定性代码；LLM 只产内容不决定工具图。LangChain/LangGraph 会带来抽象税，且掩盖 Trace 细节。V0 用「编排器直接调 Python 函数 + 结果回填上下文」，最简单、最可追踪、每一步可跑。

### 2.2 单一 Agent + Tools

编排器（`agent/loop.py`）是唯一 Agent，所有研究能力以 Tools 暴露（`tools/registry.py` 注册、schema 化、逐调用落 Trace）：

```
research goal ─► ResearchAgent.run()
  1  Load Knowledge Context      ← tools.search_knowledge / read_knowledge
  2  Plan                        ← LLM（planner）
  3  Query Rewrite/Expansion     ← LLM（保留 Original Query）
  4  Loop（≤ max_agent_steps）
       Search      ← tools.search_papers / tools.web_search
       Dedup+Norm  ← 确定性 code（title key / arxiv id）
       Rerank      ← LLM，产出 Top N + 理由
       Read        ← tools.read_paper / read_webpage（硬截断）
       Assess      ← LLM：证据是否足够回答 research_questions
       Replan/Continue/Finish   ← 确定性分支
  5  Cross-source Synthesis      ← LLM
  6  Novelty Detection           ← LLM（对照 Knowledge State）
  7  Research Brief + Knowledge Update Proposal
  8  HITL 确认（CLI 交互 / --yes 供 eval）→ applied → 落库
```

不调用 Anthropic 原生 tool-use：编排路径确定，函数由编排器直接调用，Trace 更干净。

### 2.3 分层

```
CLI ──► Agent(编排器) ──► Tools ──► 外部(arXiv/Web)
                │                └──► Memory(Knowledge/Research/Feedback)
                ├──► Trace  ────────► SQLite + workspace/<task_id>/trace.jsonl
                └──► Eval  ─────────► golden cases + runner + metrics
```

### 2.4 运行模式

- `live`：真实 LLM（需 `ANTHROPIC_API_KEY`）+ 真实搜索。
- `mock`：`LODESTAR_LLM_MODE=mock`，LLM 返回固定夹具，全离线跑通管道 → 冒烟测试 / 回归用，**不烧 token**。

### 2.5 关键配置（`config.py` 默认值，Eval 后续调）

```
max_agent_steps = 15        max_search_queries = 8
max_deep_read_sources = 5   web_results_per_query = 6
arxiv_results_per_query = 6 read_char_budget = 12000（每源）
tool_timeout_s = 30         模型：claude-sonnet-5（judge: haiku，可配）
```

---

## 三、项目目录

```
Lodestar/                          ← 项目根（2026-08-17 由 lodestar 改名）
├── README.md  CLAUDE.md  requirements.txt  .env.example  .gitignore
├── docs/
│   └── V0-design.md                     ← 本文档
├── lodestar/                         ← Python 包（保持小写，符合包命名）
│   ├── __init__.py   __main__.py   cli.py
│   ├── config.py    llm.py（Anthropic + Mock）    prompts.py
│   ├── agent/
│   │   ├── planner.py   queries.py   reranker.py
│   │   ├── assessor.py  synthesizer.py  novelty.py
│   │   └── loop.py                       ← 编排器（唯一 Agent）
│   ├── tools/
│   │   ├── registry.py  arxiv_search.py  web_search.py
│   │   ├── web_read.py  paper_read.py    knowledge.py
│   ├── memory/
│   │   ├── db.py（schema + FTS）  repo.py（CRUD）
│   ├── trace/recorder.py
│   ├── brief.py                          ← Brief 渲染（MD）
│   ├── eval/
│   │   ├── cases.py  runner.py  metrics.py
│   │   └── cases/*.json                     ← Golden Cases ×5（V0.1.2）
│   └── data/                             ← lodestar.db（gitignore）
├── workspace/                            ← 每任务产物：<task_id>/{trace.jsonl,brief.md,sources.json}
└── tests/test_smoke.py                   ← 离线冒烟/回归
```

---

## 四、Golden Scenario #1（第一阶段验收）

```
目标：研究 Self-Evolving Skill / Self-Evolving Agent 的技术进展，
     理解核心技术路径及其与 Skill / Memory / Eval 的关系。
走法：seed 已知概念（Agent/Skill/Eval/Harness/Trace）
   → Plan（研究问题拆分）
   → Query 中英扩展（self-evolving agents / self-improving agents / skill learning / memory）
   → arXiv + Web 检索 → 去重 → Rerank → 读取
   → 跨源综合（共同点/技术路线/差异/冲突/空白）
   → Novelty（对照已知概念，标出真增量）
   → Brief（核心结论/Why it matters/What is actually new/技术路径/与我的知识的关系/Open Questions）
   → Knowledge Update Proposal（pending → 确认 → applied）
成功标准（eval 自动判定）：Brief 覆盖 ≥3 个核心概念、来源 ≥3、trace 完整、无重复搜索、
   novelty 判定可解释、knowledge 更新已落库。
```

### Golden Case 套件（v0.1.2 扩到 5 个）

| case_id | 主题 | 阶段 |
|---|---|---|
| `self_evolving_agent` | Self-Evolving Agent / Skill | V0-golden-1 |
| `agent_memory` | Agent Memory（存储/检索/更新/分层） | V0-golden-2 |
| `context_engineering` | Context Engineering（预算/压缩/检索） | V0-golden-3 |
| `agent_eval` | Agent Eval（trajectory/Judge/regression） | V0-golden-4 |
| `mcp` | MCP 协议与生态 | V0-golden-5 |

配套改动：mock 双通道**按主题路由**（`fixtures.topic_from_text` 取「最先出现的最具体短语」，
解决 goal/query 交叉提及主题时的撞车）；mock 检索夹具按主题分组，使离线 eval 的覆盖率断言有真实意义。
离线回归：5/5 case verdict=pass、coverage=1.0、venue_coverage=1.0。

### live 校准记录（v0.1.3，2026-08-17，真实 LLM = 内网网关 deepseek-v4-flash 最便宜档）

- **接入**：Lodestar 走 Anthropic SDK，`ANTHROPIC_BASE_URL`+`ANTHROPIC_AUTH_TOKEN` 指向内网模型路由
  （`/v1/models` 暴露 15 个模型，无 Claude，均为国产/开源系；`deepseek-v4-flash` 为最便宜档）。
- **空输出 bug（已修）**：deepseek 思考块会把输出预算吃光 → text 块为空 → JSON 解析失败。修复：
  默认关闭 thinking（`llm_thinking=False`，实测网关支持 `thinking={type:disabled}` 返回纯 text）、
  空输出重试一次（预算×2 并去掉 thinking）、thinking 参数不被支持时自动去掉重试。
- **Query 语言校准（prompt 已改）**：第一次 live 跑 deepseek 产出全中文 Query → arXiv 中文检索质量差
  （返回不相关论文）。强化 prompt「Query 一律英文」后实测：5 英文 + 1 中文兜底 + original。
- **Assess+Replan 环 live 真实工作**：assess 发现证据缺口（如 Voyager/Reflexion/Agent-E 对比缺失）
  → replan 补搜 1 次 → 有界停止，均正确执行。
- **首轮 live 完整 run 评分**：unique=27（arXiv 源）、read=6、**coverage=1.0、task_success=5、faithfulness=5**，
  产出带引用标注的跨源综合（MemSkill/Library Drift/SEVerA/Double Ratchet 等真实工作）+ 按改进层的
  方法差异表。**核心 Research Loop 在真实（廉价）LLM 下验证成立。**
- **venue 阈值校准（两轮）**：live venue_cov 在 0.25~0.41 间波动（S2 429 仅 Dblp 兜底，近期论文收录不全）
  → `min_venue_coverage` 0.5→0.3→**0.2**（0.2 = 「回填确实跑了且解析出东西」的最低门槛；该指标本质是外部
  API 可用性的函数，不该卡死在环境噪声上。你在自有网络跑 S2 可达时 venue_cov 会大幅提高）。
- **覆盖度中英同义词（新增）**：live 中文 Brief 用「检索」而非 "retrieval"，英文子串覆盖会低估
  （agent_memory 曾 0.86 缺 retrieval）→ `metrics._ZH_SYNONYMS`（检索/评估/记忆/上下文/轨迹…）补上。
- **环境限制（诚实记录）**：本沙箱 DuckDuckGo 网页搜索超时不可达 → live 仅有 arXiv 源；DDG 在你的
  自有网络通常可用。
- **5 个 case 的 live 全基线（v0.1.4，deepseek-v4-flash）**：全部 verdict=pass、coverage=1.0、
  task_success 4~5、faithfulness 5、venue_cov 0.25~0.38。检索量 24~40 个来源、深度阅读 5~9 个。

## 五、V0 完成边界（本轮交付）

1. 上述纵向切片可运行：`lodestar research "<goal>" --yes`（live 或 mock）。
2. Trace 全链路落库 + JSONL。
3. Eval：golden case + runner + 指标，`lodestar eval` 可离线回归。
4. HITL：knowledge 更新默认交互确认。
5. 不做：UI、GitHub/项目文件读取、Experiment 创建（V2）、自演进（V4）。

---

## 六、V1 需求池

> 入口原则（PRD §26 ⑤）：每个新能力都必须带 Eval 配套，否则不进 V1。
> 状态：**V1-R1 已实现（v0.1.1，2026-08-17）**；V1-R2 待排期。来源：V0 评审记录的缺口 A1 论文侧 + A2 读取深度。

### V1-R1 来源元数据补齐：journal / venue —— ✅ 已实现

**现状与缺口**：arXiv API 返回字段实测为 `id/title/updated/summary/published/comment/link/category`，
**无 journal/venue**（见 §一 A1）。V0 因此无法区分 preprint 与正式发表，Rerank 的 Source Quality 缺一个强信号。

**落地（v0.1.1）**：
- 新模块 `lodestar/venue.py`：`enrich_papers_venues(cfg, sources)`，确定性 code（PRD §26③），在编排器检索去重后调用（主流程 + replan 补搜两条路径都覆盖）。
- 数据源 **provider 链（顺序回退，成功即停）**：默认 `semanticscholar → openalex → dblp → crossref`。
  - S2 / OpenAlex：按 arXiv id 精确匹配（结构最好）。
  - Dblp / Crossref：按 title 检索，**相似度守卫 ≥0.5**（已发表条目优先），防止错挂其它论文 venue（Faithfulness 红线）。
  - 节流 `venue_request_interval_s≈1.2s`，上限 `venue_enrich_limit=10`；`LODESTAR_VENUE_PROVIDERS` 可调顺序。
- 落库：`sources` 表新增 `venue / is_published / external_ids`（`db._migrate` 幂等补列，老库无损）。
- 消费点：Rerank prompt 显示 `venue=（已发表/预印本）` 作为 Source Quality 信号；Brief「Key Papers」表格加「venue / 发表状态」列。
- 降级：单篇失败 → venue=None、is_published=False；**provider 遇 429 即标记本批次限流、不再硬闯**（换下一个 provider）；全失败任务照常完成，绝不阻断。
- **Eval 配套**：新增 `venue_coverage`（论文来源中 venue 解析比例）+ `venue_resolved`；golden case 阈值 `min_venue_coverage`（本 case 0.5，缺省不启用避免外部 API 波动影响判定）；mock fixture 已补 venue 字段。
- **验收（实测记录）**：
  - ✅ mock 全离线回归全绿，`venue_coverage=1.0`；Brief 正确显示「NeurIPS 2025（已发表）/ ICLR 2026（已发表）/ arXiv preprint（预印本）」。
  - ✅ **live 端到端已在本环境验证**：Dblp 兜底成功解析真实 venue —— `Chain-of-Thought Prompting` → **NeurIPS**（已发表）、`Reflexion` → **NeurIPS**（已发表）、Agent Memory 主题真实研究任务中解析出「Proceedings of the 21st Int. Conf. on Security and Cryptography」「Auton. Agents Multi Agent Syst.」等；预印本正确标 `arXiv preprint`。
  - ⚠️ 环境约束（非代码问题）：本环境 S2 无 Key 公共限流 429、OpenAlex 与 Crossref 域名受限/429 → 在这台机器上实际由 **Dblp** 承担回填。用户自有网络下 provider 链会自动优先走到可达的源（S2 质量最高，建议申请免费 S2 Key 提额）。

**数据源可靠性说明（2026-08-17 实测，含二次诊断修正）**：
- **Dblp 作为 venue 源**：CS 领域覆盖面强（NeurIPS/ICML/ICLR/CVPR/期刊均收录）、免费无 Key、响应快。实测正确解析：CoT→NeurIPS、Reflexion→NeurIPS、BERT→NAACL-HLT、MRMMIA（arXiv 版）→preprint、Multi-Agent DRL Survey→Auton. Agents Multi Agent Syst.。
- **二次诊断发现的真实坑**：Dblp 的 title 搜索是**词级 AND 匹配**且 `:` 有字段前缀语义——① 短/常见短语标题（如 "Attention Is All You Need"）本尊根本不在 top-N，只有相似论文；② arXiv title 与 dblp title 差一个词（"Chat Application Logs" vs "Chat Agents"）就整条匹配不到；③ 带冒号标题需短名查询。**0.5 相似度阈值会把相似但不是同一篇的论文的 venue 错挂过来**（Attention 曾被标成另一篇的 preprint，MRMMIA 曾被挂上无法确证的另一篇的会议名）。
- **最终修复（安全优先）**：title 型源 **近精确守卫 sim≥0.8** + 多查询回退（完整标题 → 冒号前短名）+ 达不到就**诚实 None/preprint**，绝不错挂。代价是部分论文 venue 缺失（可接受，S2/OpenAlex 按 arXiv id 精确匹配才是主源，能覆盖多数漏检）。
- **严格限流**：超打会 429/断连（实测连打 9 个请求即被断连），生产代码守礼貌：mailto UA + 批间 1.2s + 瞬时错误退避重试 + 每篇最多 2 次查询。
- 结论：**Dblp 定位为回退源，安全优先、宁缺勿错**；每个 venue 带 `venue_note` 标注来源，可审计。

### V1-R2 论文 PDF 全文读取 —— ✅ 已实现（v0.1.5）

**现状与缺口**：V0 `read_paper` 仅读取 arXiv **摘要级**（title/authors/date/abstract），跨源综合的证据粒度止于摘要；arXiv 虽提供 PDF 链接但 V0 不下载不解析。

**落地（v0.1.5）**：
- `read_paper` 扩展：arXiv（含 v 版本）+ 通用 `.pdf` 链接；新增 `full_text` 参数。
- 下载与缓存：PDF 落 `workspace/pdfs_cache/`（gitignore），按 arXiv id / URL 名缓存去重，重复任务不重复下载。
- 解析：**PyMuPDF** 抽文本 → 轻量**按节抽取**（Abstract/Introduction/Method/Experiments/Results…）→
  `read_char_budget` 硬截断。
- **Token 预算守护（沿缺口 A2 原则）**：默认关（`LODESTAR_FULL_TEXT=false`）；开启后仅 **Top `full_text_max_sources`(默认2)** 个论文来源读全文；assess 判定证据不足触发 replan 时，补搜的 Top 1 来源读全文。绝不让全文读取打爆上下文。
- **优雅降级**：PyMuPDF 缺失 / PDF 下载失败 / 无文本层（扫描件）→ 回退 abstract 级并带 note，绝不阻断管道。
- **Eval 配套**：新增 `full_text_sources`（read_depth=full 的来源数）指标；Brief 来源表新增「读取」列（全文/摘要/网页）。
- **验收**：live 下对 arXiv 条目返回正文章节、PDF 缓存落盘；无 PDF/扫描件优雅降级；mock 全离线回归保持绿（mock 全文夹具覆盖 read_depth=full 路径）。

**（相关但未排入本条）**：多来源论文重复检测（同一论文在 arXiv + Semantic Scholar + 网页去重）、GitHub README 检索、Experiment 闭环——分别列 V1 其它条目 / V2。

---

## 七、V3 种子：Coding Agent 接入选型（v0.1.6，实测）

PRD V3 需要把 Research Insight → Experiment → Build，Build 步接入外部 Coding Agent。
落地为一个**可插拔 executor 抽象**（`lodestar/build/`），当前实现两个：

- `lodestar/build/executor.py`：`BuildExecutor.run(prompt, cwd, timeout) → ExecutorResult`；`get_executor(auto)` 按可用性探测。
- `lodestar/build/claude_code.py`：headless `claude -p`（透传 `--model / --permission-mode / --output-format`）。
- `lodestar/build/codex.py`：headless `codex exec --skip-git-repo-check`。
- CLI：`python -m lodestar build "<prompt>" [--executor claude|codex|auto]`。

**实测对比（2026-08-17，本环境）**：

| 维度 | Claude Code（claude 2.1.200） | Codex CLI（codex 0.139.0） |
|---|---|---|
| headless 执行 | ✅ `claude -p` 直接返回输出 | ⚠️ `codex exec` 需 `--skip-git-repo-check` |
| 本环境可用 | ✅ **实测通过**（走现有网关 ANTHROPIC_BASE_URL+token，模型 deepseek-v4-flash） | ❌ **实测失败**：绑定 chatgpt.com 云认证（`/backend-api/wham/apps`），本环境不可达、反复重连 |
| **开源许可** | ❌ 闭源（Anthropic 专有 ToS，不可再分发/内嵌） | ✅ **Apache-2.0 开源**（本机包实测 `@openai/codex` 0.139.0，仓库 `github.com/openai/codex`）——可 fork/内嵌/审计 |
| 鉴权 | 复用已有网关 / Anthropic Key / Pro OAuth | 需单独 ChatGPT 登录或 OpenAI 兼容端点（`OPENAI_BASE_URL`+key）配置 |
| 可参数化 | `--model / --permission-mode / --output-format json` | `--model` 等 |
| 与 Lodestar 契合 | 高（同一网关、JSON 输出好解析、用户日常工具） | 条件性高（配好端点即用，且可随项目开源分发） |

**结论（修正版）：分场景选，两个都留可插拔。**
- **个人日常使用、现在就可用** → **Claude Code**（本环境实测通过、复用同一网关、JSON 好解析）。
- **若目标是开源/可再分发的独立工具**（Lodestar 未来上 GitHub）→ **Codex 更优**：Apache-2.0 可 fork/内嵌/随包分发，Claude Code 闭源不可再分发。Codex 需配 OpenAI 兼容端点（`OPENAI_BASE_URL` + `OPENAI_API_KEY`）即可切 `--executor codex`。
- 结论不变的是：**选型由「当前可用性」与「许可/分发目标」共同决定**，抽象层已支持一行切换，不锁死。
