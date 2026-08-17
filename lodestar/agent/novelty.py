"""Novelty Detection（PRD §12）：相对用户 Knowledge State，本次研究新在哪里。"""
from __future__ import annotations

from lodestar import prompts

EMPTY = {"claims": [], "overall_novelty": "unknown", "note": "novelty 判定失败，已降级"}


def detect(cfg, llm, goal: str, synthesis: str, knowledge_ctx: list[dict]) -> dict:
    system, user = prompts.novelty_prompt(cfg, goal, synthesis, knowledge_ctx)
    try:
        data = llm.complete_json("novelty", system, user)
    except Exception as e:  # noqa: BLE001
        out = dict(EMPTY)
        out["note"] = f"novelty 判定失败: {e}"
        return out
    claims = []
    for c in data.get("claims") or []:
        claims.append({
            "claim": str(c.get("claim") or ""),
            "concept": str(c.get("concept") or ""),
            "novelty": c.get("novelty") if c.get("novelty") in {"high", "medium", "low"} else "medium",
            "is_repackaging_of": c.get("is_repackaging_of"),
            "reason": str(c.get("reason") or ""),
        })
    overall = data.get("overall_novelty") if data.get("overall_novelty") in {"high", "medium", "low"} else "medium"
    return {"claims": claims, "overall_novelty": overall}
