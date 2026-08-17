"""Query Rewrite / Expansion（PRD §8.3）：保留 original，生成多路检索 Query。"""
from __future__ import annotations

from lodestar import prompts


def expand_queries(cfg, llm, goal: str, plan: dict, knowledge_ctx: list[dict]) -> list[dict]:
    system, user = prompts.queries_prompt(cfg, goal, plan, knowledge_ctx)
    data = llm.complete_json("queries", system, user)
    queries = [q for q in (data.get("queries") or []) if q.get("text")]
    # 保证 original 始终保留在结果里
    original = data.get("original") or goal
    if not any(q["text"].strip().lower() == original.strip().lower() for q in queries):
        queries.insert(0, {"text": original, "purpose": "原始目标（保留原意）"})
    return queries[:cfg.max_search_queries]
