# Lodestar — AI 前沿技术 Research & Build Workspace Agent (V0)

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
```

## 产物位置

- 数据库（Knowledge State / Research Memory / Trace / Eval runs）：`lodestar/data/lodestar.db`
- 每任务产物：`workspace/<task_id>/{brief.md, sources.json, trace.jsonl}`
- Eval 隔离库：`lodestar/data/eval_lodestar.db`

## 能力边界（诚实声明）

- ✅ **V1-R1 已实现（v0.1.1）**：论文 **journal/venue 已补齐**——检索后并联 provider 链（Semantic Scholar → OpenAlex → Dblp → Crossref）回填 `venue / is_published`，进 Rerank 的 Source Quality 与 Brief 表格；429 限流/不可达自动换源，单篇失败优雅降级（不阻断任务），基于 title 的源带相似度守卫。**live 已实测**（Dblp 兜底：CoT/Reflexion 解析到 NeurIPS）。详见 `docs/V0-design.md` §六。
- 论文读取仍为 **abstract 级**；`read_paper` 仅支持 arXiv；**PDF 全文读取未做**（V1-R2 待排期）。
- Web 搜索后端为 DuckDuckGo Lite（零 Key）；搜索后端接口化，可换 Brave/Serper。
- `knowledge rollback` 只恢复 status/confidence，追加的笔记作为审计痕迹保留。
- 未做：UI、Experiment 闭环、GitHub/项目文件检索、自演进（V4）、自动过期重审（B4 缺口，V1）。
- **Mock 模式（`--mock` / `--offline`）输出为管道验证夹具，不代表真实研究质量。**

## Roadmap

- **V0（当前）**：Research —— Plan → Search → Rerank → Read(abstract) → Synthesis → Knowledge Update，含 Trace + Eval。
- **V1（进行中）**：V1-R1 journal/venue 补齐 ✅；V1-R2 PDF 全文读取（PyMuPDF，Top 1~2 来源，token 预算守护）；Weekly AI Frontier Research。
- **V2+**：Research → Experiment 闭环；Build 接入；Self-Evolving Skill（PRD §25）。
