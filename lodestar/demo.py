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
from lodestar.relevance import score_project_relevance


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


DEMO_SIGNAL_DETAILS = {
    "demo-frontier-weekly": [
        {"concept": "可信记忆门控", "explanation": "记忆不是越多越好，Agent 需要在写入上下文前判断相关性、来源和风险。", "project_relation": "对应 Lodestar 的 Knowledge State、记忆复核和 Memory Trust Gate，是当前最直接的产品能力缺口。", "pdf_url": "https://arxiv.org/pdf/2608.20202"},
        {"concept": "Skill 的跨任务迁移", "explanation": "可复用经验应拆成粒度合适的 Skill，而不是把一次成功轨迹原样复制。", "project_relation": "可以把 Lodestar 的 Research Trace 和 Eval Gate 连接起来，筛选真正能迁移的 Skill candidate。", "pdf_url": "https://arxiv.org/pdf/2608.20274"},
        {"concept": "预算感知的上下文选择", "explanation": "工具和 Skill 的选择要同时考虑任务收益、冗余和 Token 成本，而不只是逐条做相似度 Top-K。", "project_relation": "对应 Lodestar 的 Tool Registry、Harness 和 Context Budget，可直接转成一个可评估的选择器实验。", "pdf_url": "https://arxiv.org/pdf/2608.19993"},
    ],
    "demo-ls-001": [
        {"concept": "中期工具使用训练", "explanation": "Tool Calling 的训练数据可以来自真实 API、MCP Skill 和文档工作流，而不只依赖最终问答。", "project_relation": "Lodestar 已经记录工具调用、结果和失败轨迹，下一步可以沉淀成 tool-use eval 数据集。", "pdf_url": "https://arxiv.org/pdf/2608.20314"},
        {"concept": "工具 affordance 与参数 grounding", "explanation": "Agent 不仅要选对工具，还要理解工具能做什么、参数如何补齐，以及失败后如何恢复。", "project_relation": "可以扩展 Lodestar 的 MCP Registry，为每个工具增加 affordance、参数完整性和 failure recovery 测试。", "pdf_url": "https://arxiv.org/pdf/2608.20314"},
        {"concept": "失败轨迹也是训练信号", "explanation": "只保存成功调用会让模型学到理想路径，保留失败样本才能评估真实的工具使用能力。", "project_relation": "Research Trace 可以成为带有错误类型、恢复动作和最终结果的可审计训练样本。", "pdf_url": "https://arxiv.org/pdf/2608.20314"},
    ],
    "demo-ls-002": [
        {"concept": "相关记忆导致推理固化", "explanation": "即使记忆与问题相关，也可能把当前推理锁定在错误路径上。", "project_relation": "Lodestar 需要在 memory/repo.py 到 agent/loop.py 之间加入 Trust Gate，而不是直接注入召回结果。", "pdf_url": "https://arxiv.org/pdf/2608.20202"},
        {"concept": "同源记忆不能重复计票", "explanation": "多条记忆如果都来自同一个上游证据，不能被当作多个独立支持。", "project_relation": "项目的记忆审计轨迹需要记录 provenance，避免 Knowledge State 把重复来源误判成共识。", "pdf_url": "https://arxiv.org/pdf/2608.19701"},
        {"concept": "记忆决策的四个风险维度", "explanation": "召回前要同时判断相关性、来源独立性、冲突风险和时效性。", "project_relation": "这四项可以成为 Lodestar 的 Memory Trust Gate 字段，并进入 Eval 的 memory trap、false-majority 和任务正确率指标。", "pdf_url": "https://arxiv.org/pdf/2608.20202"},
    ],
    "demo-ls-003": [
        {"concept": "完整任务级 Skill 可能过拟合", "explanation": "完整任务轨迹携带太多任务特定细节，跨任务迁移时容易产生负迁移。", "project_relation": "Lodestar 可以先从 Research Trace 中拆出可复用的子任务能力，再决定是否进入 Skill Library。", "pdf_url": "https://arxiv.org/pdf/2608.20274"},
        {"concept": "子任务级 Skill 的粒度平衡", "explanation": "好的 Skill 要在可执行的具体性和跨任务抽象性之间取得平衡。", "project_relation": "Eval Gate 可以同时记录 specificity、abstractness 和跨任务收益，避免一次成功就自动晋升。", "pdf_url": "https://arxiv.org/pdf/2608.20274"},
        {"concept": "执行前的 Skill utility 诊断", "explanation": "Skill 是否值得加载，应结合 Skill 描述和当前任务做执行前诊断。", "project_relation": "这可以接入 Lodestar 的 Skill Registry，在真正调用前记录选择理由和预期收益。", "pdf_url": "https://arxiv.org/pdf/2608.20274"},
    ],
    "demo-ls-004": [
        {"concept": "逐条 Top-K 不等于最优组合", "explanation": "单条相似度最高的 Skill 放在一起，组合后可能有冗余，整体收益反而下降。", "project_relation": "Lodestar 的 Skill 检索可以从逐条排序升级为带预算约束的集合选择实验。", "pdf_url": "https://arxiv.org/pdf/2608.19993"},
        {"concept": "上下文冗余需要显式惩罚", "explanation": "重复 Skill 会占用上下文并干扰执行，选择器需要把冗余作为成本。", "project_relation": "可以在 Tool Registry 和 Research Trace 中记录重复率、Token 成本与被排除候选的原因。", "pdf_url": "https://arxiv.org/pdf/2608.19993"},
        {"concept": "选择器要解释收益与成本", "explanation": "每次选择都应说明任务收益、Token 成本和未入选候选的理由。", "project_relation": "这会让 Lodestar 的 Harness 具备可审计的上下文决策轨迹，方便后续做 Eval。", "pdf_url": "https://arxiv.org/pdf/2608.19993"},
    ],
}


DEMO_PAPER_NOTES = {
    "https://arxiv.org/pdf/2608.20202": {
        "summary": "这篇论文构建了一个专门测试 LLM 记忆使用陷阱的基准，把记忆是否相关、是否会造成推理固化和信念偏移放在同一套任务里比较。",
        "finding": "真实且相关的记忆并不总是有益；在其测试设置中，受测记忆策略整体低于 no-memory 基线，最强方案也出现明显下降。",
    },
    "https://arxiv.org/pdf/2608.19701": {
        "summary": "这篇论文研究多 Agent 如何仲裁记忆，重点不是简单统计记忆条数，而是追踪每条记忆背后的上游来源和来源之间的相关性。",
        "finding": "多个记忆如果共享同一上游证据，就会形成 Memory Correlation Bias；系统需要估计独立证据数量，避免把同源信息误判成多数共识。",
    },
    "https://arxiv.org/pdf/2608.20274": {
        "summary": "这篇论文比较 task-level、subtask-level 以及 text/code 等不同 Skill 形态，研究什么粒度和格式更容易迁移到未见任务。",
        "finding": "子任务级、文本化的 Skill 在可执行性和跨任务抽象之间更容易取得平衡，完整任务轨迹反而更容易携带任务特定细节。",
    },
    "https://arxiv.org/pdf/2608.19993": {
        "summary": "这篇论文把 Skill 选择建模为带 Token 和上下文惩罚的集合优化问题，同时考虑组合收益、冗余和预算约束。",
        "finding": "逐条相似度 Top-K 并不等于最优组合；预算感知的选择器可以用更少上下文换取更高任务收益，并解释为什么排除候选。",
    },
    "https://arxiv.org/pdf/2608.20314": {
        "summary": "这篇论文探索在模型中期训练阶段合成 Agent 工具使用数据，把网页、PDF、代码、真实 API、MCP Skill 和文档工作流统一成训练样本。",
        "finding": "工具能力的提升不只来自最终答案监督，工具 affordance、参数 grounding 和失败恢复轨迹本身也应该进入训练与评测。",
    },
}


def _demo_relevance_text(task: dict, link: dict) -> str:
    return " ".join([
        task.get("goal", ""),
        task.get("takeaway", ""),
        " ".join(task.get("opportunities") or []),
        link.get("query", ""),
    ])


def _signal_source_title(task: dict, pdf_url: str) -> str:
    target = (pdf_url or "").replace("/pdf/", "/abs/")
    for title, url, _ in task.get("sources") or []:
        if url == target and not title.lower().endswith(" pdf"):
            return title
    for title, url, _ in task.get("sources") or []:
        if url == target:
            return title.removesuffix(" PDF")
    return "原始论文"


def _brief(task: dict, project: dict | None = None, code_matches: list[dict] | None = None) -> str:
    signal_details = [
        {**item, "source_title": _signal_source_title(task, item["pdf_url"])}
        for item in DEMO_SIGNAL_DETAILS.get(task["id"], [])
    ]
    if signal_details:
        signals = "\n\n".join(
            f"### {index:02d} · {item['concept']}\n"
            f"**论文讲了什么**：{DEMO_PAPER_NOTES.get(item['pdf_url'], {}).get('summary', '这篇论文围绕该 Agent 能力提出了新的评测和实现方法。')}\n\n"
            f"**关键发现**：{DEMO_PAPER_NOTES.get(item['pdf_url'], {}).get('finding', '论文结果提示需要在真实任务和可审计轨迹上进一步验证。')}\n\n"
            f"**概念**：{item['explanation']}\n\n"
            f"**与当前项目的关系**：{item['project_relation']}\n\n"
            f"**关键来源**：{item['source_title']}\n"
            f"[查看 PDF 原文 ↗]({item['pdf_url']})"
            for index, item in enumerate(signal_details, 1)
        )
    else:
        signals = "\n".join(f"- {item}" for item in task["signals"])
    opportunities = "\n".join(f"- **\u53ef\u9a8c\u8bc1\u65b9\u5411**\uff1a{item}" for item in task["opportunities"])
    link = DEMO_PROJECT_LINKS.get(task["id"], {})
    project = project or DEMO_PROJECTS[0]
    paths = [match.get("path") for match in (code_matches or []) if match.get("path")]
    if not paths:
        paths = link.get("files") or []
    code_evidence = "\n".join(f"  - `{item}`" for item in paths[:4]) or "  - 暂未命中项目代码"
    relevance = score_project_relevance(
        _demo_relevance_text(task, link),
        project,
        evidence_count=len(paths),
    )
    score_breakdown = relevance["breakdown"]
    return (
        f"# {task['goal']}\n\n"
        f"> **\u4e00\u53e5\u8bdd\u7ed3\u8bba**\uff1a{task['takeaway']}\n\n"
        "## 关键信号\n\n"
        f"{signals}\n\n"
        "## 项目关联\n\n"
        f"- **当前项目**：`{project['name']}`\n"
        f"- **项目关联度**：**{relevance['score']}/100（{relevance['level']}）**\n"
        f"- **评分构成**：技术栈命中 {score_breakdown['technology_stack']}/35 · 项目语境命中 {score_breakdown['project_context']}/25 · 代码证据 {score_breakdown['code_evidence']}/25 · 进行中状态 {score_breakdown['active_status']}/15\n"
        f"- **观察到的缺口**：{link.get('gap', '暂未记录项目缺口。')}\n"
        f"- **关联原因**：{link.get('fit', '该研究方向与当前项目画像匹配。')}\n"
        f"- **接入位置**：{link.get('integration', '研究 → 知识 → 实验')}\n"
        "- **命中的项目代码**：\n"
        f"{code_evidence}\n\n"
        "## 项目机会\n\n"
        f"{opportunities}\n\n"
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
            relevance = score_project_relevance(
                _demo_relevance_text(item, link), canonical_project, evidence_count=len(code_matches)
            )
            repo.create_task(
                conn, item["id"], item["goal"],
                {"demo": True, "demo_release": DEMO_RELEASE, "source_window": "2026-08-15/2026-08-21",
                 "project": canonical_project["name"], "project_query": link["query"]},
                queries=[item["goal"]], llm_mode="mock",
            )
            repo.finish_task(conn, item["id"], _brief(item, canonical_project, code_matches), "finished", metrics={
                "demo": True, "demo_release": DEMO_RELEASE, "source_count": len(item["sources"]),
                 "project": canonical_project["name"], "project_matches": len(code_matches),
                 "project_relevance_score": relevance["score"],
                 "project_relevance_breakdown": relevance["breakdown"],
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
                "relevance_score": relevance["score"], "score_breakdown": relevance["breakdown"],
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
