"""Project Relevance：把研究得到的「可验证方向」映射到用户真实项目。

用户愿景：最新技术 × 我的项目现状 自动结合。这里做核心映射——
每条机会判断它适用于用户的哪些「进行中」项目（基于技术栈/领域），并给出理由。
"""
from __future__ import annotations

from lodestar.llm import LLMClient


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
    return {"mappings": data.get("mappings") or []}
