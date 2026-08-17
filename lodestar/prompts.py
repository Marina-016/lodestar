"""所有 Agent Prompt 集中于此。

约定：
- system prompt 首行固定 `# ROLE: <role>`，供 Mock 夹具匹配、Trace 可读、Eval 审计。
- 输出 JSON 的 role，返回体 schema 见各函数 docstring。
- 技术术语保留英文原词，叙述语言跟随 brief_language。
"""
from __future__ import annotations

from lodestar.config import Config
from lodestar.llm import SYSTEM_ROLE_MARKER

SYSTEM_BASE = (
    "你是 Lodestar（导星）——面向 AI 从业者的个人 AI 前沿技术研究 Agent。"
    "你的用户具备 Agent / Skill / Harness / Memory / Eval / Context Engineering 等基础认知，"
    "不需要从 LLM 基础开始解释。"
    "你只输出要求的 JSON 结构或 markdown，不输出多余解释。\n\n"
)


# ----------------------------------------------------------------------
# 上下文渲染（Knowledge State / 来源列表）
# ----------------------------------------------------------------------
def render_knowledge_ctx(concepts: list[dict]) -> str:
    """把 Knowledge State 渲染进 prompt 的上下文片段。"""
    if not concepts:
        return "（用户 Knowledge State 为空：本次不假设用户已知任何概念，Novelty 判定降级为相对空库。）"
    lines = ["## 用户已知概念（Knowledge State）", ""]
    for c in concepts:
        notes = "；".join(c.get("notes", []) or [])
        related = "、".join(c.get("related", []) or [])
        line = f"- {c['name']} [status={c.get('status')}, confidence={c.get('confidence')}]"
        if notes:
            line += f" — 笔记: {notes}"
        if related:
            line += f" — 相关: {related}"
        lines.append(line)
    return "\n".join(lines)


def render_sources_block(sources: list[dict]) -> str:
    """把候选来源渲染成带 index 的列表（index 从 0 开始，供 rerank 引用）。

    含 V1-R1 venue 信息（期刊/发表状态），作为 Source Quality 信号。
    """
    lines = ["## 候选来源（index 从 0 开始）", ""]
    for i, s in enumerate(sources):
        date = s.get("date") or "n/a"
        authors = "、".join(s.get("authors", []) or [])[:80]
        snippet = (s.get("snippet") or "")[:220].replace("\n", " ")
        venue = s.get("venue")
        venue_txt = ""
        if venue:
            pub = "已发表" if s.get("is_published") else "预印本/preprint"
            venue_txt = f" | venue={venue}（{pub}）"
        lines.append(f"{i}. [{s['title']}] ({s['url']}) | {s.get('source_type')} | {date} | {authors}{venue_txt}")
        if snippet:
            lines.append(f"   摘要: {snippet}")
    return "\n".join(lines)


# ----------------------------------------------------------------------
# 1. Research Planner（§8.2）
# ----------------------------------------------------------------------
def plan_prompt(cfg: Config, goal: str, knowledge_ctx: list[dict]):
    system = SYSTEM_ROLE_MARKER.format(role="planner") + "\n\n" + SYSTEM_BASE + (
        "你负责把一个模糊的研究目标拆解成可执行的研究计划。"
        "计划必须动态生成、切中用户的知识基础，不要输出固定模板。\n"
        "只输出如下 JSON：\n"
        "{\n"
        '  "goal": "规范化后的研究目标",\n'
        '  "research_questions": ["要回答的研究问题，3-6 个，按优先级排序"],\n'
        '  "search_strategy": ["检索策略，说明查什么、去哪查、查几类"],\n'
        '  "expected_output": ["研究完成后应交付的内容清单"]\n'
        "}"
    )
    user = f"## 研究目标\n{goal}\n\n{render_knowledge_ctx(knowledge_ctx)}\n\n请输出研究计划 JSON。"
    return system, user


# ----------------------------------------------------------------------
# 2. Query Rewrite / Expansion（§8.3）
# ----------------------------------------------------------------------
def queries_prompt(cfg: Config, goal: str, plan: dict, knowledge_ctx: list[dict]):
    system = SYSTEM_ROLE_MARKER.format(role="queries") + "\n\n" + SYSTEM_BASE + (
        "你负责把研究目标重写并扩展为一组检索 Query。\n"
        "要求：\n"
        "- 必须保留 original（用户原始目标，避免改写改变意图）。\n"
        "- 生成 3-6 个 Query，**一律使用英文**（AI 前沿论文/资料几乎全英文，中文 Query 在 arXiv 上检索质量差）；"
        "最多允许 1 个中文兜底 Query。\n"
        "- 每个 Query 注明 purpose（它想召回什么证据）。\n"
        "- 不要重复搜索同一个意思。\n"
        "只输出如下 JSON：\n"
        "{\n"
        '  "original": "原始目标",\n'
        '  "queries": [{"text": "query", "purpose": "目的"}] \n'
        "}"
    )
    user = (
        f"## 研究目标\n{goal}\n\n## 研究计划\n{plan}\n\n"
        f"{render_knowledge_ctx(knowledge_ctx)}\n\n请输出扩展 Query 列表 JSON。"
    )
    return system, user


# ----------------------------------------------------------------------
# 3. Rerank（§9）
# ----------------------------------------------------------------------
def rerank_prompt(cfg: Config, goal: str, questions: list[str], sources: list[dict], knowledge_ctx: list[dict]):
    system = (
        SYSTEM_ROLE_MARKER.format(role="rerank") + "\n\n" + SYSTEM_BASE +
        "你负责把候选来源按对用户的价值排序，并给出可解释理由。\n"
        "排序至少考虑：Topic Relevance / Recency / Source Quality / Novelty to User / Project Relevance。\n"
        "Source Quality 请参考来源行里的 venue 字段：已发表的期刊/会议（如 NeurIPS/ICLR）通常比纯 preprint 更可信，"
        "可作为加分信号；无 venue 标注的按未知处理，不要臆测。\n"
        "参考用户已有 Knowledge State：若某来源只是重复用户已懂内容，应降低 Novelty 分。\n"
        "只输出如下 JSON：\n"
        "{\n"
        '  "ranked": [{"index": 来源index, "score": 1-10, "reason": "一句话理由"}] \n'
        "}\n"
        f"按分数从高到低排列，最多给 {cfg.rerank_top_n} 条。"
    )
    user = (
        f"## 研究目标\n{goal}\n\n## 研究问题\n" + "\n".join(f"- {q}" for q in questions) + "\n\n"
        f"{render_sources_block(sources)}\n\n{render_knowledge_ctx(knowledge_ctx)}\n\n请输出排序结果 JSON。"
    )
    return system, user


# ----------------------------------------------------------------------
# 4. Evidence 评估（§17 loop 的 Assess 步骤）
# ----------------------------------------------------------------------
def assess_prompt(cfg: Config, goal: str, questions: list[str], evidence: str):
    system = SYSTEM_ROLE_MARKER.format(role="assess") + "\n\n" + SYSTEM_BASE + (
        "你负责判断当前证据是否足以回答研究问题。\n"
        "只输出如下 JSON：\n"
        "{\n"
        '  "sufficient": true/false,\n'
        '  "gaps": ["仍未覆盖的研究问题或证据缺口"],\n'
        '  "decision": "synthesize" | "replan",\n'
        '  "reason": "一句话理由"\n'
        "}\n"
        "decision=synthesize 表示证据足够；decision=replan 表示需要补搜（gaps 将用于生成补充 Query）。"
    )
    user = f"## 研究目标\n{goal}\n\n## 研究问题\n" + "\n".join(f"- {q}" for q in questions) + f"\n\n## 已收集证据\n{evidence}\n\n请输出评估 JSON。"
    return system, user


# ----------------------------------------------------------------------
# 5. Cross-source Synthesis（§13）
# ----------------------------------------------------------------------
def synthesis_prompt(cfg: Config, goal: str, questions: list[str], read_sources: list[dict], knowledge_ctx: list[dict]):
    system = SYSTEM_ROLE_MARKER.format(role="synthesis") + "\n\n" + SYSTEM_BASE + (
        "你负责对已深入阅读的来源做跨来源综合分析。"
        "禁止写成『Paper A 讲什么、Paper B 讲什么』，必须形成结构化结论。\n"
        "输出 markdown，至少包含以下小节（语言：{lang}）：\n"
        "- ## 共同观点：多个来源一致认同的结论\n"
        "- ## 主要技术路线：归纳 2-4 条技术路径\n"
        "- ## 方法差异：各工作改进的是哪一层（Prompt / Skill / Memory / Policy / Tool 策略）\n"
        "- ## 相互冲突：来源间的分歧与争议\n"
        "- ## 研究空白：仍未被解决/较少被研究的问题\n"
        "- ## 当前趋势：整体方向\n"
        "每个结论必须标注支持它的来源标题（方括号引用），没有来源支撑的判断不要写。"
    ).format(lang="中文" if cfg.brief_language == "zh" else "English")
    body = []
    for s in read_sources:
        body.append(f"## 来源: {s['title']}  ({s['url']})\n{(s.get('content') or s.get('snippet') or '')[:cfg.read_char_budget]}\n")
    user = (
        f"## 研究目标\n{goal}\n\n## 研究问题\n" + "\n".join(f"- {q}" for q in questions) + "\n\n"
        + "\n".join(body)
        + f"\n\n{render_knowledge_ctx(knowledge_ctx)}\n\n请输出跨来源综合分析（markdown）。"
    )
    return system, user


# ----------------------------------------------------------------------
# 6. Novelty Detection（§12）
# ----------------------------------------------------------------------
def novelty_prompt(cfg: Config, goal: str, synthesis: str, knowledge_ctx: list[dict]):
    system = SYSTEM_ROLE_MARKER.format(role="novelty") + "\n\n" + SYSTEM_BASE + (
        "你负责判定本次研究相对用户已有知识到底新在哪里。\n"
        "规则：\n"
        "- high = 真正的新技术/新概念/新关系；medium = 已有概念的实质性延伸；low = 已有概念的重新包装。\n"
        "- 如果只是再次提出用户已知的观点，必须标 low 并说明是哪个已有概念。\n"
        "只输出如下 JSON：\n"
        "{\n"
        '  "claims": [\n'
        '    {"claim": "研究结论", "concept": "对应概念名(英文)", "novelty": "high|medium|low",'
        ' "is_repackaging_of": "若有重包装则填已有概念名，否则 null", "reason": "判定理由"}\n'
        "  ],\n"
        '  "overall_novelty": "high|medium|low"\n'
        "}\n"
        "claims 只列本次研究真正值得记录的 2-5 条。"
    )
    user = (
        f"## 研究目标\n{goal}\n\n## 综合分析\n{synthesis}\n\n"
        f"{render_knowledge_ctx(knowledge_ctx)}\n\n请输出 Novelty 判定 JSON。"
    )
    return system, user


# ----------------------------------------------------------------------
# 7. Eval Judge 系列（§21）
# ----------------------------------------------------------------------
JUDGE_SCALE = "评分 1-5：1=差 2=不足 3=及格 4=良好 5=优秀。"


def judge_task_success_prompt(cfg: Config, goal: str, brief: str):
    system = SYSTEM_ROLE_MARKER.format(role="judge_task_success") + "\n\n" + SYSTEM_BASE + (
        f"你是 Eval Judge。判断该 Research Task 是否真正完成了研究目标（产出有信息量的 Brief，而非空话）。{JUDGE_SCALE}\n"
        '只输出 JSON：{"score": 1-5, "rationale": "依据"}'
    )
    user = f"## 研究目标\n{goal}\n\n## Research Brief\n{brief[:12000]}\n\n请评分。"
    return system, user


def judge_faithfulness_prompt(cfg: Config, goal: str, brief: str, sources: list[dict]):
    system = SYSTEM_ROLE_MARKER.format(role="judge_faithfulness") + "\n\n" + SYSTEM_BASE + (
        f"你是 Eval Judge。检查 Brief 中的关键论断是否被所列来源支持（无来源支撑的断言越多分越低）。{JUDGE_SCALE}\n"
        '只输出 JSON：{"score": 1-5, "rationale": "依据"}'
    )
    src_block = render_sources_block(sources)
    user = f"## 研究目标\n{goal}\n\n## Research Brief\n{brief[:12000]}\n\n## 来源\n{src_block}\n\n请评分。"
    return system, user


def judge_planning_prompt(cfg: Config, goal: str, plan: dict, queries: list[dict], searches: int):
    system = SYSTEM_ROLE_MARKER.format(role="judge_planning") + "\n\n" + SYSTEM_BASE + (
        f"你是 Eval Judge。评估研究计划质量：研究问题是否切题、检索策略是否覆盖核心技术路径、是否出现明显低效/重复搜索（实际搜索次数 {searches}）。{JUDGE_SCALE}\n"
        '只输出 JSON：{"score": 1-5, "rationale": "依据"}'
    )
    user = f"## 研究目标\n{goal}\n\n## Plan\n{plan}\n\n## Queries\n{queries}\n\n请评分。"
    return system, user


def judge_novelty_quality_prompt(cfg: Config, goal: str, brief: str, knowledge_ctx: list[dict]):
    system = SYSTEM_ROLE_MARKER.format(role="judge_novelty") + "\n\n" + SYSTEM_BASE + (
        f"你是 Eval Judge。评估 Brief 对『相对用户已有知识的增量』的判定质量：是否明确区分了真增量与重包装，是否避免重复用户已懂内容。{JUDGE_SCALE}\n"
        '只输出 JSON：{"score": 1-5, "rationale": "依据"}'
    )
    user = f"## 研究目标\n{goal}\n\n## Research Brief\n{brief[:12000]}\n\n{render_knowledge_ctx(knowledge_ctx)}\n\n请评分。"
    return system, user
