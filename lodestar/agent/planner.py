"""Research planner: generate both the research plan and the tool policy."""
from __future__ import annotations

from lodestar import prompts

_ALLOWED_SEARCH_TOOLS = {"search_papers", "search_web"}


def _normalize_tool_plan(raw) -> list[dict]:
    """Keep planner-selected tools auditable and reject unknown tool names."""
    out = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("tool") or "").strip()
        if name not in _ALLOWED_SEARCH_TOOLS or name in {x["tool"] for x in out}:
            continue
        out.append({
            "tool": name,
            "purpose": str(item.get("purpose") or ""),
            "when": str(item.get("when") or ""),
        })
    return out


def plan(cfg, llm, goal: str, knowledge_ctx: list[dict]) -> dict:
    system, user = prompts.plan_prompt(cfg, goal, knowledge_ctx)
    data = llm.complete_json("planner", system, user)
    return {
        "goal": data.get("goal") or goal,
        "research_questions": [str(q) for q in (data.get("research_questions") or [])],
        "search_strategy": [str(s) for s in (data.get("search_strategy") or [])],
        "expected_output": [str(o) for o in (data.get("expected_output") or [])],
        # Empty means use the safe fallback in ResearchAgent._select_search_tools.
        "tool_plan": _normalize_tool_plan(data.get("tool_plan")),
    }