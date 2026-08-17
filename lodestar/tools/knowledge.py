"""Knowledge 相关工具：search_knowledge / read_knowledge / save_research_note / update_knowledge_proposal。

这些工具读写 Knowledge State，是对 PRD §10/§19 的落地：修改必须走 pending proposal + 确认。
"""
from __future__ import annotations

from lodestar.memory import repo
from lodestar.tools.registry import register


def tool_search_knowledge(ws, query: str, limit: int = 8):
    concepts = repo.search_concepts(ws.conn, query, limit=limit)
    return {"concepts": concepts, "note": f"Knowledge State 命中 {len(concepts)} 个概念"}


def tool_read_knowledge(ws, concept: str):
    c = repo.get_concept(ws.conn, concept)
    if c is None:
        return {"error": f"Knowledge State 中无概念: {concept}", "concept": concept}
    return {"concept": c}


def tool_save_research_note(ws, concept: str, note: str):
    """把研究笔记挂到某概念下（不存在则创建为 partial/low）。"""
    existing = repo.get_concept(ws.conn, concept)
    if existing is None:
        c = repo.upsert_concept(ws.conn, concept, status="partial", confidence="low", append_note=note)
    else:
        c = repo.upsert_concept(ws.conn, concept, append_note=note)
    return {"concept": c, "updated": True, "note": f"已给概念 {concept} 追加研究笔记"}


def tool_update_knowledge_proposal(ws, concept: str, action: str, proposal: dict):
    """提出 Knowledge State 修改（PRD §19：先 proposal、后确认、再 applied）。"""
    if action not in {"create", "update"}:
        return {"error": f"action 只能是 create/update，收到 {action!r}"}
    update_id = repo.add_knowledge_update(ws.conn, ws.current_task_id, concept, action, proposal)
    return {"update_id": update_id, "status": "pending", "note": f"已登记 {action} 提案（id={update_id}），等待确认"}


register(
    name="search_knowledge",
    description="在 Knowledge State 里检索相关概念，返回 {concepts:[...]}",
    fn=tool_search_knowledge,
    parameters={"query": {"type": "string", "required": True}, "limit": {"type": "integer"}},
)
register(
    name="read_knowledge",
    description="读取单个概念的知识状态，返回 {concept:{name,status,confidence,notes,related}}",
    fn=tool_read_knowledge,
    parameters={"concept": {"type": "string", "required": True}},
)
register(
    name="save_research_note",
    description="给概念追加研究笔记（不存在则创建 partial 概念）",
    fn=tool_save_research_note,
    parameters={"concept": {"type": "string", "required": True}, "note": {"type": "string", "required": True}},
)
register(
    name="update_knowledge_proposal",
    description="登记一条 Knowledge State 修改提案（pending，待用户确认后 applied）",
    fn=tool_update_knowledge_proposal,
    parameters={"concept": {"type": "string", "required": True}, "action": {"type": "string", "required": True},
                "proposal": {"type": "object", "required": True}},
)
