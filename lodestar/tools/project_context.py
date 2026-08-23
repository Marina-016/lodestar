"""Project-context retrieval tools exposed to the MCP Harness."""
from __future__ import annotations

from lodestar.memory import repo
from lodestar.tools.registry import register


def _project_id(ws, project: str | None):
    if not project:
        return None
    if str(project).isdigit():
        return int(project)
    for item in repo.list_projects(ws.conn):
        if item["name"].lower() == str(project).lower():
            return item["id"]
    return -1


def tool_search_project_context(ws, query: str, project: str | None = None, limit: int = 6):
    project_id = _project_id(ws, project)
    docs = repo.search_project_documents(ws.conn, query, project_id=project_id, limit=limit)
    return {"matches": docs, "count": len(docs),
            "note": "Searches only explicitly indexed project files; use read_project_file for the full bounded excerpt."}


def tool_read_project_file(ws, document_id: int, char_budget: int = 6000):
    doc = repo.get_project_document(ws.conn, int(document_id))
    if not doc:
        return {"error": f"project document not found: {document_id}"}
    budget = max(500, min(int(char_budget), 12_000))
    return {"id": doc["id"], "path": doc["path"], "title": doc["title"], "url": doc.get("url"),
            "source": doc.get("source"), "content": (doc.get("content") or "")[:budget],
            "truncated": len(doc.get("content") or "") > budget}


register(name="search_project_context", description="Search explicitly indexed local/GitHub project files for a relevant implementation context.",
         fn=tool_search_project_context,
         parameters={"query": {"type": "string", "required": True}, "project": {"type": "string"}, "limit": {"type": "integer"}})
register(name="read_project_file", description="Read a bounded excerpt from one project file returned by search_project_context.",
         fn=tool_read_project_file,
         parameters={"document_id": {"type": "integer", "required": True}, "char_budget": {"type": "integer"}})
