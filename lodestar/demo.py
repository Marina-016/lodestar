"""Curated, source-backed demo data for the Lodestar recording flow.

The dataset is intentionally tied to the current week's agent-research papers.
Running lodestar demo seed refreshes only the four known demo task IDs and
does not touch user-created research history.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import shutil
import sqlite3

from lodestar.config import PROJECT_ROOT
from lodestar.context import Workspace
from lodestar.experiment import scaffold_experiment
from lodestar.memory import repo
from lodestar.project_index import index_local_project


DEMO_RELEASE = "2026-week34-trusted-memory"

DEMO_PROJECTS = [
    {
        "name": "Lodestar / Agent Research Lab",
        "url": "https://github.com/Marina-016/lodestar",
        "description": "Research agent that maps weekly AI techniques to repository evidence, user-confirmed memory, and evaluation-gated experiments.",
        "tech_stack": ["Python", "Agent", "MCP", "SQLite", "Research Trace", "Memory", "Eval"],
        "status": "active",
    },
]

DEMO_CONCEPTS = [
    ("Agent Memory Lifecycle", "known", "high", "Agent memory 需要覆盖写入、召回、更新、重审与归档，而不只是向量检索。"),
    ("Memory Provenance", "partial", "medium", "长期记忆应保留来源、时间与变更理由，才能在冲突时回溯。"),
    ("Explicit Memory Consent", "known", "high", "长期记忆写入必须经过用户确认，不由 Agent 静默改写。"),
    ("Memory Review", "partial", "medium", "过期或低置信记忆需要进入 retain、revise、archive 的人工重审流程。"),
    ("Research Trace", "known", "high", "研究步骤、工具调用、证据和状态变更需要绑定到同一条可审计轨迹。"),
    ("Skill Memory", "partial", "medium", "成功轨迹可以沉淀为 Skill candidate，但必须先验证跨任务迁移。"),
    ("Context Budget", "partial", "high", "检索结果和 Skill 都会消耗上下文预算，选择策略必须考虑边际收益。"),
    ("Eval Gate", "partial", "medium", "候选能力只有在固定对照集上通过质量与安全门槛后才能晋升。"),
]

DEMO_TASKS = [
    {
        "id": "demo-ls-001",
        "goal": "Tool Calling 为什么开始前移到模型的中期训练？",
        "days": 3,
        "takeaway": "MidTool 把真实 API、MCP Skill 与文档工作流合成为中期训练语料，说明通用 Tool Calling 不应完全依赖后训练临时学会。它对 Lodestar 的直接启发不是训练基础模型，而是把可审计工具轨迹沉淀成高质量的 tool-use eval 与训练样本。",
        "signals": [
            "Tool use 的关键不只是选中工具，还包括识别 affordance、补齐参数与错误恢复",
            "真实 API、MCP Skill 和文档工作流可以统一成可复用的交互轨迹",
            "工具轨迹需要保留失败样本，否则只会教会模型理想路径",
        ],
        "opportunities": [
            "把 Lodestar 的 MCP 调用、参数、结果和恢复动作导出为脱敏 tool-use eval 数据",
            "为每个工具增加 affordance、参数 grounding 和 failure recovery 的固定测试用例",
        ],
        "next": "先从 20 条本地 Research Trace 提取 tool-call pair，人工检查脱敏、参数完整性和失败恢复标签。",
        "sources": [
            ("MidTool: Mid-training Data Synthesis for Agentic Tool Use", "https://arxiv.org/abs/2608.20314", "8 月 20 日提交；用 Web、PDF、代码、真实 API、MCP Skill 和文档工作流构造通用工具使用语料。"),
            ("MidTool PDF", "https://arxiv.org/pdf/2608.20314", "论文在 BFCL、tau2-Bench 与 MCP Universe 上评估中期训练、SFT 和 RL 的组合。"),
            ("Lodestar 项目仓库", "https://github.com/Marina-016/lodestar", "项目已有 MCP、Tool Registry 与 Research Trace，可把运行轨迹转成固定评测数据。"),
        ],
    },
    {
        "id": "demo-ls-002",
        "goal": "可信记忆：Agent 如何判断一条记忆该不该被使用？",
        "days": 0,
        "takeaway": "本周两篇最新论文把问题从“如何记得更多”推进到“何时不该使用记忆”：MemTrapBench 发现真实且相关的记忆也会造成推理固化与信念偏移；CAMA 指出多个 Agent 可能重复引用同一上游来源，制造虚假多数。对 Lodestar 来说，召回之后、注入上下文之前需要一层可审计的 Memory Trust Gate。",
        "signals": [
            "语义相关且记录准确的记忆仍可能把当前推理锁定在错误路径上",
            "多条记忆若来自同一上游证据，不能被当作多份独立支持重复计票",
            "记忆召回需要同时判断相关性、来源独立性、冲突风险与时效性",
        ],
        "opportunities": [
            "在 memory/repo.py 的召回结果进入 agent/loop.py 前增加 Memory Trust Gate：评估相关性、来源独立性、冲突风险和时效性",
            "在 Research Trace 中记录每条记忆被 admitted、rejected 或 escalated 的理由，而不是只记录最终答案",
        ],
        "next": "用相同问题和上下文预算比较 Top-K 直接注入与 Trust Gate 两组，重点测 memory trap rate、false-majority rate、任务正确率和 Token 成本。",
        "sources": [
            ("MemTrapBench: Benchmarking Cognitive Traps in LLM Memory Use", "https://arxiv.org/abs/2608.20202", "8 月 20 日提交；所有受测记忆策略都低于 no-memory 设置，最强方案也下降超过 10%。"),
            ("MemTrapBench PDF", "https://arxiv.org/pdf/2608.20202", "论文把失败拆成 Reasoning Fixation 与 Belief Distortion，并提出推理时的 AdaptiveMem 缓解方法。"),
            ("Beyond Memory Majority: Latent-Source Reasoning for Multi-Agent Memory Arbitration", "https://arxiv.org/abs/2608.19701", "8 月 20 日提交；提出 Memory Correlation Bias 与结合来源追踪的 CAMA 仲裁框架。"),
            ("Beyond Memory Majority PDF", "https://arxiv.org/pdf/2608.19701", "论文通过估计独立证据源数量，抑制相关记忆形成的虚假多数。"),
        ],
    },
    {
        "id": "demo-ls-003",
        "goal": "什么样的 Skill Memory 才能跨任务迁移？",
        "days": 1,
        "takeaway": "Break It Down, Pass It On 发现完整任务级 Skill 经常让 Agent 低于 no-memory baseline；子任务级 Skill 平均带来提升，而且文本 Skill 比代码 Skill 更容易迁移。真正可复用的经验不是整段成功轨迹，而是粒度适当、具体性与抽象性平衡的能力单元。",
        "signals": [
            "完整任务级经验可能携带过多任务特定细节，导致跨任务负迁移",
            "子任务级 Skill 在可执行的具体性和跨任务抽象性之间更容易取得平衡",
            "Skill utility 可以在执行新任务前，仅根据 Skill 与任务描述进行诊断",
        ],
        "opportunities": [
            "把通过 Eval 的 Research Trace 拆成子任务级 text Skill candidate，再决定是否进入 Skill Library",
            "记录 specificity、abstractness 与跨任务收益，避免一次成功就自动晋升为长期 Skill",
        ],
        "next": "从 20 条成功 Trace 分别生成 task-level 与 subtask-level Skill，在未见任务上比较成功率和负迁移率。",
        "sources": [
            ("Break It Down, Pass It On: Cross-Task Skill Transfer in LLM Agents", "https://arxiv.org/abs/2608.20274", "8 月 20 日提交；系统比较 task/subtask induction 与 text/code format 对跨任务迁移的影响。"),
            ("Break It Down, Pass It On PDF", "https://arxiv.org/pdf/2608.20274", "论文提出结合 specificity 与 abstractness 的 skill utility score，可在新任务执行前诊断 Skill。"),
            ("Lodestar Research Trace", "https://github.com/Marina-016/lodestar", "项目已有可审计研究轨迹与 Eval Gate，可作为候选 Skill 的来源和晋升条件。"),
        ],
    },
    {
        "id": "demo-ls-004",
        "goal": "有限上下文里应该加载哪一组 Skill？",
        "days": 2,
        "takeaway": "Optimal Skill Selection 把 Skill 选择建模为带硬 Token 预算的集合优化，而不是逐条相似度 Top-K。论文中的 BPS 同时考虑 Skill 组合收益、冗余与上下文惩罚，在受控 BigCodeBench 变体上以更少 Token 获得更高任务成功率。",
        "signals": [
            "逐条相关性高不代表组合在一起仍有高边际收益",
            "重复 Skill 会占用上下文并可能干扰执行，需要显式惩罚",
            "选择器应同时报告任务收益、Token 成本与被排除候选的理由",
        ],
        "opportunities": [
            "把 Lodestar 的 Skill 检索从逐条 Top-K 改成预算约束下的集合选择实验",
            "在 Trace 中记录候选集合、冗余惩罚、Token 预算和最终选择理由",
        ],
        "next": "固定 8k 上下文预算，对比 semantic Top-K 与 budget-aware selection 的成功率、读取 Token 和冗余率。",
        "sources": [
            ("Optimal Skill Selection for LLM Agents with Provable Bicriteria Guarantees", "https://arxiv.org/abs/2608.19993", "8 月 20 日提交；将 Skill set selection 表述为带上下文惩罚的子模优化。"),
            ("Optimal Skill Selection PDF", "https://arxiv.org/pdf/2608.19993", "论文报告 BPS 达到 0.73 task success，并比最强已发布 router 少用 28% Token。"),
            ("Lodestar Tool Registry", "https://github.com/Marina-016/lodestar", "当前工具注册与 Harness 已可提供候选 Skill、调用结果和 Token 预算的实验接口。"),
        ],
    },
]


DEMO_FRONTIER = {
    "id": "demo-frontier-weekly",
    "goal": "本周 Agent 研究有哪些值得 Lodestar 优先验证的新进展？",
    "takeaway": "最新批次呈现出一条共同主线：长期状态和可复用能力不是越多越好，Agent 需要判断什么值得进入上下文、哪些来源真正独立、以及一次成功经验能否迁移。结合 Lodestar 当前代码，优先级最高的是可信记忆。",
    "signals": [
        f"1. 可信记忆 - {DEMO_TASKS[1]['goal']}",
        f"2. Skill 迁移 - {DEMO_TASKS[2]['goal']}",
        f"3. 上下文预算 - {DEMO_TASKS[3]['goal']}",
    ],
    "opportunities": [DEMO_TASKS[1]["opportunities"][0], DEMO_TASKS[2]["opportunities"][0]],
    "next": DEMO_TASKS[1]["next"],
    "sources": [DEMO_TASKS[1]["sources"][0], DEMO_TASKS[2]["sources"][0], DEMO_TASKS[3]["sources"][0]],
}

DEMO_PROJECT_LINKS = {
    "demo-frontier-weekly": {
        "query": "memory skill tool project",
        "gap": "Lodestar 可以扫描论文并建立项目代码索引，但每周热点还需要按照具体的实现缺口排序。",
        "fit": "当前项目画像和代码索引，让可信记忆比只关注模型训练的方向更容易落到产品实现。",
        "integration": "热点筛选 → 项目证据 → 研究简报",
        "files": ["lodestar/frontier.py", "lodestar/relevance.py", "lodestar/project_index.py"],
    },
    "demo-ls-001": {
        "query": "tool MCP trace",
        "gap": "Lodestar 会记录工具调用，但还没有把它们整理成带有恢复标签的工具使用数据集。",
        "fit": "MidTool 提供了数据契约，可以把真实 API、MCP 和文档工作流转成可复用的评测材料。",
        "integration": "MCP 服务、工具注册表与轨迹记录器",
        "files": ["lodestar/mcp_server.py", "lodestar/tools/registry.py", "lodestar/trace/recorder.py"],
        "memory_concept": "Tool-Use Training Data",
    },
    "demo-ls-002": {
        "query": "memory knowledge review provenance trace",
        "gap": "知识状态支持显式确认和复核，但召回的记忆仍会在没有信任判断的情况下进入推理。",
        "fit": "MemTrapBench 和 CAMA 指出了缺失的产品层：在记忆注入上下文前，拒绝误导、过期或来源相关的记忆。",
        "integration": "记忆仓库 → 信任门 → 研究循环 → 轨迹",
        "files": ["lodestar/memory/repo.py", "lodestar/agent/loop.py", "lodestar/trace/recorder.py", "lodestar/tools/knowledge.py"],
        "memory_concept": "Memory Trust Gate",
    },
    "demo-ls-003": {
        "query": "harness tool skill trace eval",
        "gap": "Lodestar 可以保留成功轨迹，但还没有晋升规则，无法只把可迁移的子任务知识转成 Skill。",
        "fit": "跨任务迁移结果可以为粒度、格式和负迁移提供可量化的准入门槛。",
        "integration": "研究轨迹 → Skill 候选 → 评测门",
        "files": ["lodestar/harness/codex.py", "lodestar/trace/recorder.py", "lodestar/eval/harness.py"],
        "memory_concept": "Skill Transfer",
    },
    "demo-ls-004": {
        "query": "tool registry harness budget selection",
        "gap": "工具和 Skill 候选目前独立召回，冗余度和整体上下文成本还没有作为一个集合统一优化。",
        "fit": "预算感知的集合选择，可以把上下文组装变成带有成本和质量指标的明确产品决策。",
        "integration": "工具注册表 → 选择器 → Harness → 评测",
        "files": ["lodestar/tools/registry.py", "lodestar/harness/codex.py", "lodestar/eval/harness.py"],
        "memory_concept": "Budget-Aware Skill Selection",
    },
}


def _brief(task: dict, project: dict | None = None, code_matches: list[dict] | None = None) -> str:
    signals = "\n".join(f"- {item}" for item in task["signals"])
    opportunities = "\n".join(f"- **\u53ef\u9a8c\u8bc1\u65b9\u5411**\uff1a{item}" for item in task["opportunities"])
    sources = "\n".join(f"- [{title}]({url})\uff1a{reason}" for title, url, reason in task["sources"])
    link = DEMO_PROJECT_LINKS.get(task["id"], {})
    project = project or DEMO_PROJECTS[0]
    paths = [match.get("path") for match in (code_matches or []) if match.get("path")]
    if not paths:
        paths = link.get("files") or []
    code_evidence = "\n".join(f"  - `{item}`" for item in paths[:4]) or "  - 暂未命中项目代码"
    return (
        f"# {task['goal']}\n\n"
        f"> **\u4e00\u53e5\u8bdd\u7ed3\u8bba**\uff1a{task['takeaway']}\n\n"
        "## 关键信号\n\n"
        f"{signals}\n\n"
        "## 项目关联\n\n"
        f"- **当前项目**：`{project['name']}`\n"
        f"- **观察到的缺口**：{link.get('gap', '暂未记录项目缺口。')}\n"
        f"- **关联原因**：{link.get('fit', '该研究方向与当前项目画像匹配。')}\n"
        f"- **接入位置**：{link.get('integration', '研究 → 知识 → 实验')}\n"
        "- **命中的项目代码**：\n"
        f"{code_evidence}\n\n"
        "## 项目机会\n\n"
        f"{opportunities}\n\n"
        "## 关键来源\n\n"
        f"{sources}\n\n"
        "## 下一步\n\n"
        f"- {task['next']}\n"
    )


def _archive_current_state(conn, cfg) -> dict:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = Path(cfg.workspace_dir) / "demo_backups" / stamp
    backup_dir.mkdir(parents=True, exist_ok=False)
    db_backup = backup_dir / "lodestar-before-reset.db"
    target = sqlite3.connect(str(db_backup))
    try:
        conn.backup(target)
    finally:
        target.close()

    artifacts_dir = backup_dir / "artifacts"
    archived = []
    for child in list(Path(cfg.workspace_dir).iterdir()):
        if child.name == "demo_backups":
            continue
        if child.is_dir() and (child.name == "experiments" or re.fullmatch(r"[0-9a-f]{12}", child.name)):
            artifacts_dir.mkdir(exist_ok=True)
            shutil.move(str(child), str(artifacts_dir / child.name))
            archived.append(child.name)
    return {"backup_dir": str(backup_dir), "database": str(db_backup), "artifacts": archived}


def _clear_application_state(conn) -> None:
    tables = [
        "messages", "knowledge_updates", "memory_reviews", "feedback", "trace_events",
        "sources", "eval_runs", "experiments", "research_tasks", "project_documents",
        "projects", "conversations", "concepts",
    ]
    for table in tables:
        conn.execute(f"DELETE FROM {table}")
    conn.execute("DELETE FROM sqlite_sequence")
    conn.commit()


def seed_demo(cfg, clean: bool = False) -> dict:
    """Install the curated recording dataset; optionally reset all app state first."""
    ws = Workspace(cfg)
    conn = ws.conn
    created_tasks = 0
    refreshed_tasks = 0
    created_experiments = 0
    backup = None
    try:
        if clean:
            backup = _archive_current_state(conn, cfg)
            _clear_application_state(conn)

        canonical_project = DEMO_PROJECTS[0]
        project_id = repo.upsert_project(conn, **canonical_project)
        project_documents = index_local_project(PROJECT_ROOT)
        indexed_files = repo.replace_project_documents(conn, project_id, project_documents)
        indexed_paths = {doc["path"] for doc in project_documents}

        now = datetime.now(timezone.utc)
        demo_ids = [item["id"] for item in DEMO_TASKS]
        placeholders = ",".join("?" for _ in demo_ids)
        conn.execute(f"DELETE FROM sources WHERE task_id IN ({placeholders})", demo_ids)
        conn.execute(f"DELETE FROM knowledge_updates WHERE task_id IN ({placeholders})", demo_ids)
        conn.execute(f"DELETE FROM trace_events WHERE task_id IN ({placeholders})", demo_ids)
        conn.execute(f"DELETE FROM experiments WHERE task_id IN ({placeholders})", demo_ids)

        for index, item in enumerate(DEMO_TASKS):
            exists = conn.execute("SELECT 1 FROM research_tasks WHERE id=?", (item["id"],)).fetchone()
            link = DEMO_PROJECT_LINKS[item["id"]]
            code_matches = [{"path": path} for path in link["files"] if path in indexed_paths]
            repo.create_task(
                conn, item["id"], item["goal"],
                {"demo": True, "demo_release": DEMO_RELEASE, "source_window": "2026-08-15/2026-08-21",
                 "project": canonical_project["name"], "project_query": link["query"]},
                queries=[item["goal"]], llm_mode="mock",
            )
            repo.finish_task(conn, item["id"], _brief(item, canonical_project, code_matches), "finished", metrics={
                "demo": True, "demo_release": DEMO_RELEASE, "source_count": len(item["sources"]),
                "project": canonical_project["name"], "project_matches": len(code_matches),
                "evidence_coverage": round(0.86 + index * 0.03, 2), "novelty": "high",
            })
            created_at = (now - timedelta(days=item["days"])).isoformat(timespec="seconds")
            conn.execute("UPDATE research_tasks SET created_at=?, finished_at=? WHERE id=?",
                         (created_at, created_at, item["id"]))
            for rank, (title, url, reason) in enumerate(item["sources"], start=1):
                source_id = repo.add_source(conn, item["id"], {
                    "source_type": "paper" if "arxiv.org" in url else "web", "title": title,
                    "url": url, "snippet": reason, "query": item["goal"],
                    "date": "2026-08-20" if "2608." in url else None,
                })
                repo.update_source(conn, source_id, rank=rank, reason=reason, read_depth="abstract")
            repo.add_trace_event(conn, item["id"], 1, "demo_replay_sources", {"count": len(item["sources"])})
            repo.add_trace_event(conn, item["id"], 2, "project_context_search", {
                "project": canonical_project["name"], "query": link["query"],
                "matches": [match["path"] for match in code_matches], "mapping": link["fit"],
            })
            finish_seq = 3
            if item["id"] == "demo-ls-002":
                repo.add_trace_event(conn, item["id"], 3, "memory_risk_assessment", {
                    "factors": ["relevance", "source_independence", "conflict_risk", "recency"],
                    "finding": "Top-K relevance alone cannot decide whether memory should enter context.",
                })
                finish_seq = 4
            repo.add_trace_event(conn, item["id"], finish_seq, "demo_replay_finish", {"state": "saved_research"})
            created_tasks += 0 if exists else 1
            refreshed_tasks += 1 if exists else 0

        for name, status, confidence, note in DEMO_CONCEPTS:
            repo.upsert_concept(conn, name, status=status, confidence=confidence,
                                notes=[f"[demo baseline / {DEMO_RELEASE}] {note}"])

        experiments = [
            ("脱敏 Tool Trace 能否形成稳定的 tool-use 回归集？", "scaffolded", DEMO_TASKS[0]["id"]),
            ("Memory Trust Gate 能否在不降低正常任务正确率的前提下，减少 memory trap 与 false majority？",
             "draft", DEMO_TASKS[1]["id"]),
            ("子任务级 text Skill 是否比完整任务级 Skill 更容易跨任务迁移？",
             "scaffolded", DEMO_TASKS[2]["id"]),
            ("预算约束的 Skill 集合选择能否用更少 Token 获得更高任务成功率？",
             "draft", DEMO_TASKS[3]["id"]),
        ]
        for hypothesis, status, task_id in experiments:
            task = next(item for item in DEMO_TASKS if item["id"] == task_id)
            experiment_id = repo.add_experiment(
                conn, hypothesis, task_id=task_id, description=task["next"],
                source_claim="Project opportunity grounded in paper sources and indexed Lodestar files.",
            )
            if status == "scaffolded":
                output = scaffold_experiment(repo.get_experiment(conn, experiment_id), cfg.workspace_dir / "experiments")
                repo.set_experiment_build(conn, experiment_id, "scaffolded", str(output))
            created_experiments += 1
        conn.commit()
        return {
            "clean": clean, "backup": backup, "tasks": created_tasks,
            "refreshed_tasks": refreshed_tasks, "concepts": len(DEMO_CONCEPTS),
            "projects": len(DEMO_PROJECTS), "indexed_files": indexed_files,
            "experiments": created_experiments, "release": DEMO_RELEASE,
        }
    finally:
        ws.close()
