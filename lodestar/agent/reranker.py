"""Rerank（PRD §9）：按 Topic Relevance / Recency / Source Quality / Novelty / Project Relevance 排序。"""
from __future__ import annotations

from lodestar import prompts


def rerank(cfg, llm, goal: str, questions: list[str], sources: list[dict],
           knowledge_ctx: list[dict]) -> list[dict]:
    if not sources:
        return []
    system, user = prompts.rerank_prompt(cfg, goal, questions, sources, knowledge_ctx)
    data = llm.complete_json("rerank", system, user)
    ranked = data.get("ranked") or []
    by_score = []
    for item in ranked:
        idx = item.get("index")
        if not isinstance(idx, int) or not (0 <= idx < len(sources)):
            continue
        s = dict(sources[idx])
        s["score"] = int(item.get("score", 0))
        s["reason"] = str(item.get("reason") or "")
        by_score.append(s)
    by_score.sort(key=lambda s: s["score"], reverse=True)
    for i, s in enumerate(by_score):
        s["rank"] = i
    return by_score
