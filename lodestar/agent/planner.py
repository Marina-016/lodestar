"""Research Planner（PRD §8.2）：动态生成研究计划。"""
from __future__ import annotations

from lodestar import prompts


def plan(cfg, llm, goal: str, knowledge_ctx: list[dict]) -> dict:
    system, user = prompts.plan_prompt(cfg, goal, knowledge_ctx)
    data = llm.complete_json("planner", system, user)
    # 容错与规范化：保证关键字段存在
    return {
        "goal": data.get("goal") or goal,
        "research_questions": [str(q) for q in (data.get("research_questions") or [])],
        "search_strategy": [str(s) for s in (data.get("search_strategy") or [])],
        "expected_output": [str(o) for o in (data.get("expected_output") or [])],
    }
