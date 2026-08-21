"""Curated, source-backed demo data for the Lodestar recording flow.

The dataset is intentionally tied to the current week's agent-research papers.
Running lodestar demo seed refreshes only the four known demo task IDs and
does not touch user-created research history.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from lodestar.context import Workspace
from lodestar.memory import repo


DEMO_RELEASE = "2026-week34"

DEMO_PROJECTS = [
    {
        "name": "Marina-016/lodestar",
        "url": "https://github.com/Marina-016/lodestar",
        "description": "把学术热点变成可追溯的 Research → Knowledge → Experiment 工作流。",
        "tech_stack": ["Python", "Agent", "LLM", "SQLite", "Research UI"],
        "status": "active",
    },
    {
        "name": "Lodestar / Agent Research Lab",
        "url": "https://github.com/Marina-016/lodestar",
        "description": "围绕长期任务、记忆、技能选择与评测闭环，记录论文证据并生成可运行实验。",
        "tech_stack": ["Research Trace", "Memory", "Skill", "Eval"],
        "status": "active",
    },
]

DEMO_CONCEPTS = [
    ("Scientific Discovery Agent", "known", "high", "本周论文把科学发现型 Agent 描述为任务条件化的动态编排问题。"),
    ("Obligation Graph", "known", "high", "长任务需要显式的义务节点、验收语义和可回溯的证书。"),
    ("Adaptive Agent Memory", "partial", "high", "记忆不只是检索，还要和反思、路由、共识及风险审查一起工作。"),
    ("Reflection Loop", "partial", "medium", "反思应当产生可验证的下一步，而不是只增加一段解释文本。"),
    ("Skill Selection", "partial", "high", "长轨迹中，选择读取哪个 Skill 本身就是需要单独训练的决策。"),
    ("Selector Credit", "partial", "medium", "结果奖励会把长链路的信用稀释到 Skill 选择 token 上。"),
    ("Evaluator Blind Spots", "partial", "medium", "评测器也会有盲点，需要用反例驱动的方式暴露和补齐。"),
    ("Evidence Certificate", "known", "high", "研究结论要能连回来源、判断和验收结果，才能进入下一轮工作流。"),
]

DEMO_TASKS = [
    {
        "id": "demo-ls-001",
        "goal": "科学发现型 Agent 如何把长任务编译成可验证的研究图？",
        "days": 0,
        "takeaway": "本周的 Eureka 论文把科学发现从“让一个模型一直思考”推进到“按任务形成局部架构”：动态 obligation graph、专用 memory / tools / verifiers，以及带验收语义的证书。论文报告其递归任务实验完成 170/170 个任务并生成 3,948 个证书。",
        "signals": [
            "研究任务需要显式拆成 obligation、依赖关系和 acceptance criteria",
            "不同子任务可以拥有不同的 memory、operator、tool 和 verifier",
            "重复出现的瓶颈应触发受约束的架构升级，而不是无条件增加上下文",
        ],
        "opportunities": [
            "把 Lodestar Research Trace 的每个阶段映射成可验收的 obligation graph",
            "为每条洞察生成带来源、判断和下一步的 evidence certificate",
        ],
        "next": "先选一个真实学术主题，跑通“选题 → 来源 → 证据 → 验收条件 → 实验骨架”的最小闭环。",
        "sources": [
            ("Eureka: Task-Conditioned Meta-Agent Orchestration（PDF）", "https://arxiv.org/pdf/2608.19047", "8 月 19 日提交；提出动态 obligation graph、任务条件化的 Macro-Agent、局部工具/记忆/验证器和受约束的架构演化。"),
            ("Eureka arXiv 摘要页", "https://arxiv.org/abs/2608.19047", "原始摘要给出 170/170 递归任务、3,948 个证书和无 false acceptance 的论文报告结果。"),
            ("Lodestar 项目仓库", "https://github.com/Marina-016/lodestar", "用于把论文中的可验证节点落到当前 Research → Experiment → Build 产品主线。"),
        ],
    },
    {
        "id": "demo-ls-002",
        "goal": "Agent Memory 如何从静态检索升级为带反思的研究协作？",
        "days": 1,
        "takeaway": "本周的 AMR 论文把 agent-specific memory、reflection、external retrieval、复杂度路由和伦理审查放进同一条多智能体 QA 流程。对 Lodestar 来说，关键启发是：知识更新不应只是写入概念，而要改变下一次检索和判断路径。",
        "signals": [
            "复杂度评估决定走单 Agent、协作 Agent 还是升级流程",
            "专用记忆与反思反馈需要和外部检索共同参与，而非彼此孤立",
            "共识与 overseer 让高风险结论拥有额外的审查节点",
        ],
        "opportunities": [
            "把 Knowledge State 的 known / partial / low confidence 变成检索路由信号",
            "在 Research Trace 中记录“哪条记忆改变了哪一个研究判断”",
        ],
        "next": "先用一组有冲突来源的论文做回归：比较只检索、检索加记忆、检索加记忆加反思三种路径。",
        "sources": [
            ("Adaptive Memory and Reflection Multi-Agent System（PDF）", "https://arxiv.org/pdf/2608.19029", "8 月 19 日提交；把专用记忆、反思反馈、外部检索、复杂度路由、共识与伦理审查组合成 AMR 系统。"),
            ("Adaptive Memory and Reflection arXiv 摘要页", "https://arxiv.org/abs/2608.19029", "原始摘要说明其在 MedQA / MedMCQA 上比较了多种基线，并用消融分析记忆、反思和检索的组合效果。"),
            ("GroupMemBench：多方对话记忆基准（PDF）", "https://arxiv.org/pdf/2605.14498", "补充长期记忆的真实难点：多用户身份、知识更新、术语歧义、时间推理和拒答。"),
        ],
    },
    {
        "id": "demo-ls-003",
        "goal": "长任务里的 Skill 选择为什么需要独立的信用分配？",
        "days": 2,
        "takeaway": "SkillGate 把“读哪个 Skill”视为长轨迹中的独立策略决策，并指出 outcome reward 会产生 selector credit starvation：轨迹越长，选择动作分到的信用越少、符号也越容易错。论文报告 9B policy 在五个 agent benchmark 上从 40.8% 提升到 53.2%。",
        "signals": [
            "技能库变大后，选择读取哪个 Skill 本身成为策略瓶颈",
            "执行失败不应自动惩罚一个本来正确的 Skill 选择",
            "动作局部的 selector advantage 与执行阶段 outcome credit 应分开",
        ],
        "opportunities": [
            "为 Lodestar 的候选 Skill 记录选择理由、命中结果和后续执行质量",
            "把通过 Eval 的 Research Trace 转为下一次可复用的 Skill candidate，而不是直接晋升",
        ],
        "next": "先做一个 16 候选 Skill 的离线回放集，测选择准确率、误读率、执行成功率和平均读取数量。",
        "sources": [
            ("SkillGate：Training In-Policy Skill Selection（PDF）", "https://arxiv.org/pdf/2608.18852", "8 月 19 日提交；提出 selector credit starvation 和双信用通道，论文报告成功率由 40.8% 提升到 53.2%。"),
            ("SkillGate arXiv 摘要页", "https://arxiv.org/abs/2608.18852", "原始摘要说明其在 16-candidate slate 上减少误导候选暴露，并读取更少 Skill。"),
            ("ComponentBench：Computer-Use Agent 组件级诊断（PDF）", "https://arxiv.org/pdf/2608.18307", "本周同批提交的组件级评测，适合借鉴如何把长任务失败拆成可定位的环节。"),
        ],
    },
    {
        "id": "demo-ls-004",
        "goal": "评测器如何暴露自己的盲点，并反过来驱动研究迭代？",
        "days": 3,
        "takeaway": "Metrics That Write Themselves 把评测器看成一组可组合的小型 defect operators，用 counterexample-guided 的碰撞搜索来发现“两个答案得分相同但质量不同”的盲点。这个方向很适合 Lodestar：让 Eval 不只给分，还能告诉我们下一条规则应该测什么。",
        "signals": [
            "报告生成、研究 Brief 等开放任务通常缺少稳定的单一评分函数",
            "反例比再次请求一个更长的 judge prompt 更适合作为评测规则的作者请求",
            "评测规则应能在沙箱中运行、记录命中缺陷并回归到未见任务",
        ],
        "opportunities": [
            "从用户反馈和失败 Trace 中收集成对反例，驱动 Lodestar Eval 增加一条规则",
            "把 evidence coverage、novelty、next-step quality 拆成可回归的局部指标",
        ],
        "next": "先建立 20 条演示 Brief 的人工标注集，找出当前评分器最容易漏掉的两类缺陷，再生成第一版 operator。",
        "sources": [
            ("Metrics That Write Themselves（PDF）", "https://arxiv.org/pdf/2608.18744", "8 月 19 日提交；用 counterexample-guided abstraction refinement 演化小型 Python defect operators。"),
            ("Metrics That Write Themselves arXiv 摘要页", "https://arxiv.org/abs/2608.18744", "原始摘要报告在 MBPP+ / HumanEval+ 上用 55 行 operator 缩小 15.4% 的过滤差距。"),
            ("SESSE：Structured Decomposition for LLM-as-a-Judge（PDF）", "https://arxiv.org/pdf/2608.18303", "同周评测方向论文，提供将 judge 过程拆成结构化步骤的对照路线。"),
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
        "这条研究线与 **Marina-016/lodestar** 的 Research → Knowledge → Experiment 主路径直接相关。\n"
        "它适合在演示中展示：论文证据如何被压缩成判断，再变成下一步可运行的实验。\n\n"
        "## Project Opportunities\n\n"
        f"{opportunities}\n\n"
        "## Key Sources\n\n"
        f"{sources}\n\n"
        "## Next Moves\n\n"
        f"- {task['next']}\n"
    )


def seed_demo(cfg) -> dict:
    """Refresh the curated showcase rows without touching user-created rows."""
    ws = Workspace(cfg)
    conn = ws.conn
    created_tasks = 0
    refreshed_tasks = 0
    created_experiments = 0
    try:
        now = datetime.now(timezone.utc)
        demo_ids = [item["id"] for item in DEMO_TASKS]
        placeholders = ",".join("?" for _ in demo_ids)
        # These IDs are reserved by this module for the recording dataset.
        # Removing their old child rows prevents stale sources from mixing with
        # the refreshed papers; no non-demo task is affected.
        conn.execute(f"DELETE FROM sources WHERE task_id IN ({placeholders})", demo_ids)
        conn.execute(f"DELETE FROM knowledge_updates WHERE task_id IN ({placeholders})", demo_ids)
        conn.execute(f"DELETE FROM experiments WHERE task_id IN ({placeholders})", demo_ids)

        for index, item in enumerate(DEMO_TASKS):
            exists = conn.execute("SELECT 1 FROM research_tasks WHERE id=?", (item["id"],)).fetchone()
            repo.create_task(
                conn,
                item["id"],
                item["goal"],
                {"demo": True, "demo_release": DEMO_RELEASE, "source_window": "2026-08-15/2026-08-21"},
                queries=[item["goal"]],
                llm_mode="mock",
            )
            repo.finish_task(conn, item["id"], _brief(item), "finished", metrics={
                "demo": True,
                "demo_release": DEMO_RELEASE,
                "source_count": len(item["sources"]),
                "evidence_coverage": round(0.86 + index * 0.03, 2),
                "novelty": "high",
            })
            created_at = (now - timedelta(days=item["days"])).isoformat(timespec="seconds")
            conn.execute("UPDATE research_tasks SET created_at=?, finished_at=? WHERE id=?",
                         (created_at, created_at, item["id"]))
            for rank, (title, url, reason) in enumerate(item["sources"], start=1):
                sid = repo.add_source(conn, item["id"], {
                    "source_type": "paper" if "arxiv.org" in url else "web",
                    "title": title,
                    "url": url,
                    "snippet": reason,
                    "query": item["goal"],
                    "rank": rank,
                    "reason": reason,
                    "read_depth": "abstract",
                    "date": "2026-08-19" if "2608." in url else None,
                })
                conn.execute("UPDATE sources SET rank=?, reason=?, read_depth=? WHERE id=?",
                             (rank, reason, "abstract", sid))
            repo.add_knowledge_update(conn, item["id"], "Evidence Certificate", "update", {
                "old_status": "partial",
                "new_status": "known",
                "new_confidence": "medium",
                "claim": item["takeaway"],
                "novelty": "high",
                "evidence": item["sources"][0][0],
            })
            if exists:
                refreshed_tasks += 1
            else:
                created_tasks += 1

        for name, status, confidence, note in DEMO_CONCEPTS:
            repo.upsert_concept(conn, name, status=status, confidence=confidence,
                                notes=[f"[演示笔记 · {DEMO_RELEASE}] {note}"])

        for project in DEMO_PROJECTS:
            repo.upsert_project(conn, **project)

        experiments = [
            ("动态 obligation graph 能否提升研究任务的验收完整度？", "built", DEMO_TASKS[0]["id"]),
            ("带反思的 Knowledge State 是否能减少重复检索？", "draft", DEMO_TASKS[1]["id"]),
            ("独立的 Skill selector credit 是否提升长任务成功率？", "built", DEMO_TASKS[2]["id"]),
            ("反例驱动的 Eval operator 能否补齐 Brief 评分盲点？", "draft", DEMO_TASKS[3]["id"]),
        ]
        for hypothesis, status, task_id in experiments:
            exp_id = repo.add_experiment(
                conn,
                hypothesis,
                task_id=task_id,
                description="Lodestar demo：把本周论文中的可验证主张转成可运行实验。",
                source_claim="来自 Research Brief 的 Project Opportunities 与 Key Sources。",
            )
            if status == "built":
                repo.set_experiment_build(conn, exp_id, "built",
                                          str(cfg.workspace_dir / "experiments" / f"experiment_{exp_id}"))
            created_experiments += 1
        conn.commit()
        return {
            "tasks": created_tasks,
            "refreshed_tasks": refreshed_tasks,
            "concepts": len(DEMO_CONCEPTS),
            "projects": len(DEMO_PROJECTS),
            "experiments": created_experiments,
            "release": DEMO_RELEASE,
        }
    finally:
        ws.close()