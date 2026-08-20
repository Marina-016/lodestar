"""Curated demo workspace data for the Lodestar recording flow.

The seed is additive and idempotent: it enriches the local workspace without
deleting or rewriting existing research history.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from lodestar.context import Workspace
from lodestar.memory import repo


DEMO_PROJECTS = [
    {
        "name": "Marina-016/lodestar",
        "url": "https://github.com/Marina-016/lodestar",
        "description": "AI 前沿技术研究与 Build 工作台：把 Research、Knowledge、Experiment 串成一条可追踪闭环。",
        "tech_stack": ["Python", "Agent", "LLM", "SQLite", "Research UI"],
        "status": "active",
    },
    {
        "name": "Lodestar / Skill Evaluation Lab",
        "url": "https://github.com/Marina-016/lodestar",
        "description": "围绕 Skill、Eval、Trace 的小型实验场，用于验证研究结论能否沉淀为可复用能力。",
        "tech_stack": ["Skill", "Eval", "Trace", "A/B Test"],
        "status": "active",
    },
]

DEMO_CONCEPTS = [
    ("Knowledge State", "known", "high", "研究前先读取已有认知，研究后再提出可确认的更新。"),
    ("Evidence Graph", "partial", "medium", "把结论、来源、项目机会连成可回看的证据链。"),
    ("Project Relevance", "known", "high", "每个研究方向都要回答：它能如何推进 Lodestar 当前项目？"),
    ("Evaluation-driven Promotion", "partial", "medium", "只有通过 Eval 的候选 Skill 才能进入正式能力层。"),
    ("Research Trace", "known", "high", "记录检索、阅读、判断和知识更新，方便复盘与录屏展示。"),
    ("Context Compression", "known", "medium", "压缩不是简单删字，而是保留对下一步决策最有用的上下文。"),
    ("Experiment Hypothesis", "partial", "medium", "把洞察写成可验证假设，再用 baseline / candidate 做对照。"),
    ("Skill Lifecycle", "partial", "low", "Skill 需要创建、评估、升级、回滚和退休的生命周期。"),
]

DEMO_TASKS = [
    {
        "id": "demo-ls-001",
        "goal": "Lodestar 如何把一次研究沉淀为可复用 Skill？",
        "days": 0,
        "takeaway": "研究结果不应该停在 Brief：最小闭环是把证据链压缩成假设，再通过 Eval 决定是否 Promotion。",
        "signals": ["Research Brief 负责形成可追踪判断", "Experiment 负责把判断变成 baseline / candidate", "Eval 负责决定候选 Skill 是否值得进入能力层"],
        "opportunities": ["为 Lodestar 增加一个 Skill Promotion 面板，展示证据、指标和当前状态", "将一次通过的 Research Trace 自动生成可复用 Skill 草稿"],
        "next": "先用 3 个已有研究任务跑一轮固定 Eval，观察 Promotion 规则是否稳定。",
        "sources": [
            ("Lodestar README", "https://github.com/Marina-016/lodestar", "项目当前的 Research → Experiment → Build 闭环"),
            ("AgentBench: Evaluating LLMs as Agents", "https://arxiv.org/abs/2308.03688", "评估需要覆盖任务成功与过程质量"),
            ("DSPy: Compiling Declarative Language Model Calls", "https://arxiv.org/abs/2310.03714", "候选策略需要在指标驱动下迭代"),
        ],
    },
    {
        "id": "demo-ls-002",
        "goal": "Research → Experiment → Build：如何让 Lodestar 的研究结论真正落地？",
        "days": 1,
        "takeaway": "最适合演示的产品主线是：选题 → 研究简报 → 保存实验 → 生成骨架；每一步都留下下一步动作。",
        "signals": ["选题页负责降低研究启动成本", "Brief 负责汇总来源、洞察和项目关联", "Experiment 负责把洞察变成可执行的验证计划"],
        "opportunities": ["在 Brief 末尾增加一键保存实验的主按钮和当前项目关联", "为每个实验显示 hypothesis、baseline、candidate、Eval 四个阶段"],
        "next": "录屏时从 Weekly Frontier 选一个方向，完整走到 Experiment 页面。",
        "sources": [
            ("Lodestar V0 Design", "https://github.com/Marina-016/lodestar", "产品边界与 V3 最小闭环"),
            ("The Prompt Report", "https://arxiv.org/abs/2406.06608", "提示与上下文策略需要通过任务结果验证"),
            ("SWE-bench", "https://www.swebench.com/", "工程任务的验证必须有可重复的评测入口"),
        ],
    },
    {
        "id": "demo-ls-003",
        "goal": "Agent Memory 的证据链如何服务于下一次研究？",
        "days": 2,
        "takeaway": "Knowledge State 的价值不在于存了多少概念，而在于下一次研究能否少走弯路、快速暴露未知。",
        "signals": ["已知概念用于缩短检索路径", "partial / low confidence 概念用于生成下一轮问题", "每次更新保留来源与理由，避免知识库变成黑箱"],
        "opportunities": ["在知识库卡片上展示最近一次证据和下一步建议", "把 partial 概念自动加入 Weekly Frontier 的候选池"],
        "next": "先补一个知识概念详情抽屉，再接入 research trace 的来源回看。",
        "sources": [
            ("Generative Agents", "https://arxiv.org/abs/2304.03442", "记忆需要支持检索、反思和后续行动"),
            ("MemGPT", "https://arxiv.org/abs/2310.08560", "上下文与长期记忆需要明确的层次边界"),
            ("Lodestar Knowledge State", "https://github.com/Marina-016/lodestar", "项目内置的概念状态与人工确认机制"),
        ],
    },
    {
        "id": "demo-ls-004",
        "goal": "Weekly Frontier 如何直接驱动 Lodestar 的下一次实验？",
        "days": 3,
        "takeaway": "Weekly Frontier 不只是推荐文章，而是把知识缺口、进行中项目和可验证机会连接起来。",
        "signals": ["候选方向应带优先级与相关项目", "推荐结果应能一键填入研究问题", "研究完成后应能回写 Knowledge State 与 Experiment"],
        "opportunities": ["给每条选题增加‘为什么现在值得研究’的证据摘要", "将选题卡直接连接到研究、实验和项目三张视图"],
        "next": "把当前 3 条示例选题的卡片信息补齐，作为视频中的第一段交互。",
        "sources": [
            ("Lodestar Weekly Frontier", "https://github.com/Marina-016/lodestar", "基于 Knowledge State、近期任务和项目生成选题"),
            ("ReAct", "https://arxiv.org/abs/2210.03629", "行动与推理需要形成可观察轨迹"),
            ("Toolformer", "https://arxiv.org/abs/2302.04761", "工具调用应服务于下一步决策，而非单纯增加步骤"),
        ],
    },
]


def _brief(task: dict) -> str:
    signals = "\n".join(f"- {item}" for item in task["signals"])
    opportunities = "\n".join(f"- **可验证方向**：{item}" for item in task["opportunities"])
    sources = "\n".join(f"- [{title}]({url})：{reason}" for title, url, reason in task["sources"])
    return (
        f"# {task['goal']}\n\n"
        f"> **一句话结论**：{task['takeaway']}\n\n"
        "## Key Signals\n\n"
        f"{signals}\n\n"
        "## Project Relevance\n\n"
        "这条研究线与 **Marina-016/lodestar** 的 Research → Knowledge → Experiment 主路径直接相关。"
        "它适合在演示中展示‘结论如何变成下一步动作’。\n\n"
        "## Project Opportunities\n\n"
        f"{opportunities}\n\n"
        "## Key Sources\n\n"
        f"{sources}\n\n"
        "## Next Moves\n\n"
        f"- {task['next']}\n"
    )


def seed_demo(cfg) -> dict:
    """Add a polished, Lodestar-themed showcase dataset to the current workspace."""
    ws = Workspace(cfg)
    conn = ws.conn
    created_tasks = 0
    created_experiments = 0
    try:
        now = datetime.now(timezone.utc)
        for index, item in enumerate(DEMO_TASKS):
            exists = conn.execute("SELECT 1 FROM research_tasks WHERE id=?", (item["id"],)).fetchone()
            if exists:
                continue
            repo.create_task(conn, item["id"], item["goal"], {"demo": True},
                             queries=[item["goal"]], llm_mode="mock")
            repo.finish_task(conn, item["id"], _brief(item), "finished", metrics={
                "demo": True, "source_count": len(item["sources"]),
                "evidence_coverage": round(0.78 + index * 0.04, 2), "novelty": "high",
            })
            created_at = (now - timedelta(days=item["days"])).isoformat(timespec="seconds")
            conn.execute("UPDATE research_tasks SET created_at=?, finished_at=? WHERE id=?",
                         (created_at, created_at, item["id"]))
            for rank, (title, url, reason) in enumerate(item["sources"], start=1):
                sid = repo.add_source(conn, item["id"], {
                    "source_type": "web", "title": title, "url": url,
                    "snippet": reason, "query": item["goal"], "rank": rank,
                    "reason": reason, "read_depth": "abstract",
                })
                conn.execute("UPDATE sources SET rank=?, reason=?, read_depth=? WHERE id=?",
                             (rank, reason, "abstract", sid))
            repo.add_knowledge_update(conn, item["id"], "Evidence Graph", "update", {
                "old_status": "partial", "new_status": "known", "new_confidence": "medium",
                "claim": item["takeaway"], "novelty": "high", "evidence": item["sources"][0][0],
            })
            created_tasks += 1

        for name, status, confidence, note in DEMO_CONCEPTS:
            repo.upsert_concept(conn, name, status=status, confidence=confidence,
                                notes=[f"[演示笔记] {note}"])

        for project in DEMO_PROJECTS:
            repo.upsert_project(conn, **project)

        experiments = [
            ("把 Research Trace 转换为 Skill Promotion 候选", "built", DEMO_TASKS[0]["id"]),
            ("用 Project Relevance 排序下一周的研究机会", "draft", DEMO_TASKS[3]["id"]),
            ("比较 Knowledge State 对检索效率的影响", "built", DEMO_TASKS[2]["id"]),
        ]
        for hypothesis, status, task_id in experiments:
            exists = conn.execute("SELECT id FROM experiments WHERE hypothesis=?", (hypothesis,)).fetchone()
            if exists:
                continue
            exp_id = repo.add_experiment(conn, hypothesis, task_id=task_id,
                                         description="Lodestar demo：用一条可验证假设把研究结论推进到实验。",
                                         source_claim="由 Research Brief 的 Project Opportunities 自动提取")
            if status == "built":
                repo.set_experiment_build(conn, exp_id, "built",
                                          str(cfg.workspace_dir / "experiments" / f"experiment_{exp_id}"))
            created_experiments += 1
        conn.commit()
        return {"tasks": created_tasks, "concepts": len(DEMO_CONCEPTS),
                "projects": len(DEMO_PROJECTS), "experiments": created_experiments}
    finally:
        ws.close()