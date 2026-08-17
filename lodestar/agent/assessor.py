"""Assessor（PRD §17 Loop 的 Assess 步骤）：判断证据是否足以收尾或需补搜。"""
from __future__ import annotations

from lodestar import prompts

DEFAULT = {"sufficient": True, "gaps": [], "decision": "synthesize", "reason": "assess 失败，默认收尾"}


def assess(cfg, llm, goal: str, questions: list[str], evidence: str) -> dict:
    system, user = prompts.assess_prompt(cfg, goal, questions, evidence)
    try:
        data = llm.complete_json("assess", system, user)
    except Exception:  # noqa: BLE001 —— assess 失败不阻断，默认收尾
        return dict(DEFAULT)
    decision = data.get("decision")
    if decision not in {"synthesize", "replan"}:
        decision = "synthesize"
    return {
        "sufficient": bool(data.get("sufficient", True)),
        "gaps": [str(g) for g in (data.get("gaps") or [])],
        "decision": decision,
        "reason": str(data.get("reason") or ""),
    }
