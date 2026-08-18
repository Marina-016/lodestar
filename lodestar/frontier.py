"""V1：Weekly AI Frontier Research —— 基于 Knowledge State，LLM 主动推荐本周值得研究的话题。

PRD §25 V1：Agent 根据 Interest + Knowledge State + Recent Research 筛选「本周真正值得了解的 3 个变化」。
不自动搜索，只做 curation——输出的是「该研究什么」，不是研究结果。
"""
from __future__ import annotations

from lodestar.llm import LLMClient


def generate_frontier(cfg, llm: LLMClient, knowledge_ctx: list[dict],
                      recent_tasks: list[dict]) -> dict:
    system = (
        "# ROLE: frontier\n\n"
        "你是 Lodestar（导星）的 AI 前沿研究策展人。用户是 AI 从业者，对 Agent / Skill / Memory / "
        "Eval / Context Engineering / Harness / MCP / Self-Evolving Agent 等方向持续感兴趣。\n"
        "根据用户的 Knowledge State（已掌握的概念及其掌握程度）和近期研究历史，"
        "推荐 3 个本周值得深入研究的话题。\n"
        "要求：\n"
        "- 话题应切中用户的知识缺口（partial / unknown 的概念优先），避免推荐用户已经完全掌握的主题。\n"
        "- 每个话题包含：topic（一行标题）、why（一段话说明为什么本周值得关注）、priority（high/medium/low）。\n"
        "- 只输出 JSON，格式：{\"suggestions\": [{\"topic\": \"...\", \"why\": \"...\", \"priority\": \"high|medium|low\"}]}\n"
        "- 若用户知识库为空，按 AI 前沿领域当前热点推荐。"
    )
    ctx_lines = ["## 用户 Knowledge State"]
    if knowledge_ctx:
        for c in knowledge_ctx:
            ctx_lines.append(f"- {c['name']} [{c['status']}/{c['confidence']}]"
                             + (f" — 笔记：{'; '.join(c['notes'][-2:])}" if c.get('notes') else ""))
    else:
        ctx_lines.append("（空：用户尚未声明已掌握的概念）")
    ctx_lines.append("")
    ctx_lines.append("## 近期研究历史")
    if recent_tasks:
        for t in recent_tasks[:5]:
            ctx_lines.append(f"- {t['goal'][:80]}（{t.get('created_at', '')[:10]}）")
    else:
        ctx_lines.append("（无）")
    user = "\n".join(ctx_lines)
    data = llm.complete_json("frontier", system, user)
    return {
        "suggestions": [
            {"topic": s.get("topic", ""), "why": s.get("why", ""),
             "priority": s.get("priority", "medium")}
            for s in (data.get("suggestions") or [])
        ]
    }