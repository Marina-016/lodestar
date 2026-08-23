# Lodestar — AI 前沿技术 Research & Build Workspace Agent (v0.4.7)

**Agent 名：Lodestar（导星）** —— 引导研究方向、追踪 AI 前沿。

面向 AI 从业者的个人研究 Agent：`Research → Learn → Knowledge`（Build/Experiment 在 V1+）。
核心不是"总结论文"，而是维护 **Research State / Knowledge State**，持续回答 *"这次相比你知道的，新在哪里"*。

设计文档见 [`docs/V0-design.md`](docs/V0-design.md)（含 PRD 缺口审查与架构决策）。

## 快速开始

```bash
python -m pip install -r requirements.txt
cp .env.example .env        # 填入 ANTHROPIC_API_KEY（live 模式必需）
```

**离线冒烟（不烧 token、不联网，验证管道）**
```bash
python -m lodestar research "<研究目标>" --mock --offline --yes
python -m unittest tests.test_smoke -v
```

**真实运行**
```bash
python -m lodestar research "研究最近 Self-Evolving Skill / Self-Evolving Agent 有哪些值得关注的技术进展，理解核心技术路径，以及它和 Skill、Memory、Eval 的关系。"
```

**用内网模型网关（无需 Anthropic Key）**：Lodestar 走 Anthropic SDK，可把 `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN` 指向任何 Anthropic 兼容网关（如内部模型路由 `sh-dtrouter.datayes.com`），模型名用网关 `/v1/models` 暴露的 id（如 `deepseek-v4-flash`）经 `LODESTAR_MODEL` 指定，judge 同理用 `LODESTAR_JUDGE_MODEL`。`LODESTAR_LLM_THINKING=false` 可关闭思考块（更便宜、防空输出）。

## 常用命令

```bash
# 研究
python -m lodestar research "<目标>"            # 交互式：Knowledge 更新需确认
python -m lodestar research "<目标>" --yes      # 自动应用知识更新
python -m lodestar research "<目标>" --offline  # 检索也走夹具（全离线复现）

# Eval（Golden Case 回归）
python -m lodestar eval --mock --offline        # 离线回归
python -m lodestar eval --case self_evolving_agent   # 或 --case agent_memory / context_engineering / agent_eval / mcp

# Knowledge State
python -m lodestar knowledge list
python -m lodestar knowledge search self-evolv
python -m lodestar knowledge seed "Agent,Skill,Eval,Harness,Trace" --status known --confidence high
python -m lodestar knowledge diff <task_id>     # 看某 task 的知识更新提案
python -m lodestar knowledge rollback <update_id>

# Trace / 反馈
python -m lodestar trace <task_id>
python -m lodestar feedback

# V3：调 Coding Agent CLI 执行（默认 codex 开源；须配 LODESTAR_CODEX_BASE_URL + LODESTAR_CODEX_API_KEY 走内网网关，
#      未配则默认拒绝——防误烧 ChatGPT Plus 额度；--executor claude 可切）
python -m lodestar build "给这个项目写个 README"

# V3：Research → Experiment → Build（闭环）
python -m lodestar experiment list
python -m lodestar experiment save <task_id>          # 从 Brief 的 Project Opportunities 提取
python -m lodestar experiment build <exp_id> --out /tmp/exp  # scaffold + codex 实现 baseline/candidate
python -m lodestar experiment build <exp_id> --scaffold-only  # 仅确定性骨架

# V1：Weekly AI Frontier Research（基于 Knowledge State + 进行中项目推荐本周该研究什么）
python -m lodestar frontier            # 可选 --save 存报告

# Projects：GitHub 摄入 + 进行中状态（研究/选题会参考 active 项目）
python -m lodestar project add https://github.com/owner/repo [--status active]   # 可多个链接
python -m lodestar project list
python -m lodestar project status <id> active|paused|archived|idea

# Web UI（本地单页仪表盘，零依赖，默认 http://127.0.0.1:8123）
lodestar ui                            # 或 python -m lodestar ui --port 8123
```

## Codex Harness / MCP

### 当前接入的 Harness

本项目接入的是 **OpenAI Codex CLI 的开源 Harness**，不是 Claude Code Harness，也不是另一个独立的 Agent 框架。Codex Harness 负责对话上下文、工具选择、工具调用和会话执行；Lodestar 负责研究领域能力、知识库、Trace 和研究结果存储。

当前架构是：

```text
用户对话
   ↓
Codex CLI Harness（可自主判断下一步）
   ↓ MCP stdio
Lodestar MCP Server
   ├─ search_papers / search_web
   ├─ read_paper / read_webpage
   ├─ search_knowledge / read_knowledge
   ├─ save_research_note / update_knowledge_proposal
   └─ 将工具调用写回当前 Research Trace
   ↓
Knowledge State / Research Memory / Workspace
```

Lodestar 原有的 `ResearchAgent` 仍然保留。当网页对话未开启 Codex Harness、Codex 不可用或调用失败时，系统会自动回退到原有 Research Loop，保证 V0 demo 仍然可运行。

### 启动和注册 MCP

从 Lodestar 仓库根目录启动工具服务：

```bash
python -m lodestar mcp
```

在 Codex 中注册：

```bash
codex mcp add lodestar -- python -m lodestar mcp
```

检查注册状态：

```bash
codex mcp get lodestar
```

### 网页对话如何切换

默认配置仍为稳定的 Lodestar Research Loop：

```env
LODESTAR_CONVERSATION_HARNESS=loop
```

如果要让网页里的对话由 Codex 自主选择搜索、阅读和知识库工具，设置：

```env
LODESTAR_CONVERSATION_HARNESS=codex
```

Codex 模式下，每次对话会获得一个 Lodestar MCP 工具服务，并通过 `LODESTAR_MCP_TASK_ID` 将工具调用记录回该研究任务的 Trace。只有用户明确要求“记住”时，Harness 才应该调用知识写入工具。

这套设计参考了 [OpenAI Codex](https://github.com/openai/codex) 的开源 CLI 和 [Codex agent loop](https://openai.com/index/unrolling-the-codex-agent-loop/)；Lodestar 没有复制 Codex 源码，而是通过 MCP 接入其 Harness。
## 产物位置

- 数据库（Knowledge State / Research Memory / Trace / Eval runs）：`lodestar/data/lodestar.db`
- 每任务产物：`workspace/<task_id>/{brief.md, sources.json, trace.jsonl}`
- Eval 隔离库：`lodestar/data/eval_lodestar.db`

## 部署到另一台电脑（可移植性）

**代码完全可移植**（无硬编码路径/凭据）。新机器上三步：

```bash
# 1) 拿到代码：git clone 仓库 或 直接拷贝项目文件夹（排除 .git / data / workspace）
# 2) 装依赖
python -m pip install -r requirements.txt        # 或：python -m pip install -e .（可在任意目录运行）
# 3) 配凭据
cp .env.example .env   # 填 ANTHROPIC_API_KEY，或用内网网关 ANTHROPIC_BASE_URL + ANTHROPIC_AUTH_TOKEN + LODESTAR_MODEL
# 运行
cd <项目目录> && python -m lodestar research "<目标>"      # 或安装后任意目录：lodestar research "…"
```

**每台机器要重新配置的（不在代码里）**：
- `.env`：模型网关地址 + token / Anthropic Key（`.env` 已 gitignore，不会随代码走）
- Build 步的可选 CLI：`claude` / `codex`（不装也能用 research；build 需要时再装）
- 网络：网关需可达（内网）；DDG/S2/OpenAlex 在普通网络通常比本沙箱更可用
- 可选迁移：拷贝 `lodestar/data/lodestar.db` 可把已 seed 的 Knowledge State 带过去

**已验证**（实测）：`pip install -e .` 后从任意目录运行正常，数据/产物仍落在仓库内（不会写进 site-packages）。

## 能力边界（诚实声明）

- ✅ **V1-R1 已实现（v0.1.1）**：论文 **journal/venue 已补齐**——检索后并联 provider 链（Semantic Scholar → OpenAlex → Dblp → Crossref）回填 `venue / is_published`，进 Rerank 的 Source Quality 与 Brief 表格；429 限流/不可达自动换源，单篇失败优雅降级（不阻断任务），基于 title 的源带相似度守卫。**live 已实测**（Dblp 兜底：CoT/Reflexion 解析到 NeurIPS）。详见 `docs/V0-design.md` §六。
- ✅ **V1-R2 已实现（v0.1.5）**：**PDF 全文读取**——`read_paper` 支持 arXiv + 通用 `.pdf`；下载缓存到 `workspace/pdfs_cache/`（gitignore），PyMuPDF 解析后**按节递进**（Abstract/Introduction/Method/Experiments）；**token 预算守护**：默认关（`LODESTAR_FULL_TEXT=true` 开启，仅 Top 2 来源读全文，assess 证据不足补搜时读 Top 1）；PyMuPDF 缺失/下载失败/扫描件 → 优雅降级回 abstract 级。
- Web 搜索后端为 DuckDuckGo Lite（零 Key）；搜索后端接口化，可换 Brave/Serper。
- `knowledge rollback` 只恢复 status/confidence，追加的笔记作为审计痕迹保留。
- 当前仍未做：GitHub/项目文件内容检索、自演进（V4）、自动过期重审（B4 缺口）。UI、项目登记、Weekly Frontier 与 Experiment 闭环已在本版本提供。
- **Mock 模式（`--mock` / `--offline`）输出为管道验证夹具，不代表真实研究质量。**

## Roadmap

- **v0.4.7（当前发布候选）**：Research —— Plan → Search → Rerank → Read → Synthesis → Knowledge Update，含 Trace + Eval；并提供 UI、Projects、Weekly Frontier 与 Experiment → Build 闭环。
- **V1/V4 后续**：更强的 GitHub/项目文件检索、自演进与自动过期重审。
- **V3 最小闭环（已实现 ✅）**：Research Brief → Project Opportunities → Save Experiment → Scaffold + codex Build（A/B eval harness）。

## V0 Demo Preview

Lodestar v0 is designed around one traceable research loop:

Weekly Frontier → Research Brief → Evidence Trace → Knowledge State → Experiment → Build

### Run the local demo

PowerShell commands:

    $env:LODESTAR_DEMO_REPLAY="true"
    python -m lodestar demo reset
    python -m lodestar ui --port 8123 --no-browser

Open http://127.0.0.1:8123/.

The demo workspace includes:

- five current arXiv papers grouped into trusted memory, Skill transfer, context budgeting and tool use;
- one clean Lodestar project index and eight baseline Knowledge State concepts;
- a project-grounded trusted-memory workflow with explicit user confirmation;
- light/dark theme switching with persistent preference;
- an auditable trace from paper evidence and code matches to memory risk assessment;
- localized history timestamps and a demo-only history filter;
- a trusted-memory experiment comparing direct Top-K injection with a Memory Trust Gate.

The canonical recording script is [docs/demo-recording-v2.md](docs/demo-recording-v2.md); environment preparation and acceptance checks are in [docs/demo-recording-runbook.md](docs/demo-recording-runbook.md).

Replay mode is visibly labelled in the UI. It does not represent a live model run and does not change the normal live/Luna or local Codex Harness configuration.


## Project-grounded retrieval

Index only bounded text files from a registered local project, then make project implementation answers retrievable through the MCP tools search_project_context and read_project_file:

    python -m lodestar project index <id> --path <local-project-path>
    python -m lodestar project search "knowledge update" --id <id>

The index excludes hidden files, generated dependency folders, and binary content. It is an explicit snapshot: automatic refresh and vector retrieval remain future work.


## Memory lifecycle and executable experiments

Memory is not only written: it can be listed for review, explicitly retained, flagged, or archived with an audit record.

    python -m lodestar knowledge review --days 30
    python -m lodestar knowledge decide "Concept" archive --reason "superseded by newer evidence"

Experiments now distinguish a generated scaffold from a completed measured run.

    python -m lodestar experiment build <id> --scaffold-only --out experiments
    python -m lodestar experiment run <id>

A run stores machine-readable baseline, candidate, metric deltas and a pass or fail verdict. Only a passing run is marked built.
