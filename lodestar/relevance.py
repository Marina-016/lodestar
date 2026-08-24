"""Project Relevance：把研究得到的「可验证方向」映射到用户真实项目。

用户愿景：最新技术 × 我的项目现状 自动结合。这里做核心映射——
每条机会判断它适用于用户的哪些「进行中」项目（基于技术栈/领域），并给出理由。
"""
from __future__ import annotations

import re

from lodestar.llm import LLMClient


def _terms(text: str) -> set[str]:
    """Extract stable English/Chinese terms for a transparent overlap score."""
    return {
        token.lower()
        for token in re.findall(r"[A-Za-z][A-Za-z0-9+#.-]{1,}|[\u4e00-\u9fff]{2,}", text or "")
        if token.lower() not in {"the", "and", "with", "from", "this", "that", "into", "for", "project"}
    }


def score_project_relevance(research_text: str, project: dict, evidence_count: int = 0) -> dict:
    """Calculate a reproducible 0-100 project association score.

    The score is intentionally not an LLM judgment:
    - technology-stack overlap: 35 points;
    - project name/description overlap: 25 points;
    - indexed implementation evidence: 25 points;
    - active project status: 15 points.
    """
    research_terms = _terms(research_text)
    stack_terms = _terms(" ".join(project.get("tech_stack") or []))
    context_terms = _terms(" ".join([
        project.get("name") or "",
        project.get("description") or "",
    ]))
    stack_hits = sorted(research_terms & stack_terms)
    context_hits = sorted(research_terms & context_terms)
    breakdown = {
        "technology_stack": min(35, len(stack_hits) * 10),
        "project_context": min(25, len(context_hits) * 5),
        "code_evidence": min(25, max(0, int(evidence_count)) * 8),
        "active_status": 15 if project.get("status") == "active" else 0,
    }
    score = min(100, sum(breakdown.values()))
    level = "高" if score >= 75 else "中" if score >= 50 else "低"
    return {
        "score": score,
        "level": level,
        "breakdown": breakdown,
        "matched_terms": {"technology_stack": stack_hits, "project_context": context_hits},
    }


def assess_relevance(cfg, llm: LLMClient, opportunities: list[str], projects: list[dict]) -> dict:
    if not opportunities or not projects:
        return {"mappings": [], "note": "无机会或无项目，跳过关联"}
    system = (
        "# ROLE: project_relevance\n\n"
        "你是 Lodestar（导星）的项目关联评估员。把研究得到的「可验证方向」映射到用户的真实项目。\n"
        "规则：\n"
        "- 只映射到**技术栈或领域真正相关**的项目；无关就不映射（宁可少，不要牵强）。\n"
        "- 每个 mapping 说明这条方向为什么适用该项目（结合项目技术栈/描述）。\n"
        "只输出 JSON：\n"
        '{"mappings": [{"opportunity_index": 0, "applicable": ["项目名"], "reason": "为什么适用"}]}\n'
        "opportunity_index 对应用户消息里机会列表的下标（从 0 起）。"
    )
    opp_block = "\n".join(f"[{i}] {o}" for i, o in enumerate(opportunities))
    proj_block = "\n".join(
        f"- {p['name']}（status={p.get('status')}）描述:{p.get('description','')[:80]} 技术栈:{','.join(p.get('tech_stack',[]))}"
        for p in projects
    )
    user = f"## 可验证方向\n{opp_block}\n\n## 用户项目\n{proj_block}\n\n请输出关联映射。"
    data = llm.complete_json("project_relevance", system, user)
    mappings = data.get("mappings") or []
    for mapping in mappings:
        idx = mapping.get("opportunity_index")
        opportunity = opportunities[idx] if isinstance(idx, int) and 0 <= idx < len(opportunities) else ""
        names = set(mapping.get("applicable") or [])
        candidates = [project for project in projects if project.get("name") in names]
        if not candidates:
            candidates = projects[:1]
        scores = []
        for project in candidates:
            result = score_project_relevance(opportunity, project)
            scores.append({"project": project.get("name"), **result})
        if scores:
            mapping["project_scores"] = scores
            best = max(scores, key=lambda item: item["score"])
            mapping["relevance_score"] = best["score"]
            mapping["relevance_level"] = best["level"]
            mapping["score_breakdown"] = best["breakdown"]
    return {"mappings": mappings}
