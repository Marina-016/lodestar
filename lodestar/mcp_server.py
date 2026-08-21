"""Minimal MCP stdio bridge for the Lodestar tool registry.

The bridge deliberately has no third-party MCP dependency. It speaks the
small JSON-RPC surface needed by Codex: initialize, tools/list, and
tools/call. Lodestar remains responsible for domain logic and persistence;
the external harness decides when to call these tools.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any

from lodestar.config import Config, load_config
from lodestar.context import Workspace
from lodestar.memory import repo
from lodestar.tools import TOOLS, call_tool  # noqa: F401 - imports register built-ins


SERVER_INFO = {"name": "lodestar", "version": "0.4.7"}
PROTOCOL_VERSION = "2024-11-05"


def _schema(parameters: dict[str, dict[str, Any]]) -> dict[str, Any]:
    properties = {}
    required = []
    for name, spec in parameters.items():
        item = {k: v for k, v in spec.items() if k != "required"}
        properties[name] = item
        if spec.get("required"):
            required.append(name)
    result: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        result["required"] = required
    return result


def _tool_def(tool: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": tool["name"],
        "description": tool["description"],
        "inputSchema": _schema(tool["parameters"]),
    }


def _reply(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def _text_result(value: Any) -> dict[str, Any]:
    is_error = isinstance(value, dict) and bool(value.get("error"))
    return {
        "content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}],
        "isError": is_error,
    }


def handle_request(request: dict[str, Any], ws: Workspace) -> dict[str, Any] | None:
    """Handle one MCP JSON-RPC request; return None for notifications."""
    request_id = request.get("id")
    method = request.get("method")
    params = request.get("params") or {}

    if not isinstance(params, dict):
        return _error(request_id, -32602, "params must be an object")
    if not method:
        return _error(request_id, -32600, "Invalid Request")
    if request_id is None and method.startswith("notifications/"):
        return None

    if method == "initialize":
        return _reply(request_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
            "instructions": (
                "Use Lodestar tools for research discovery, paper/web reading, and knowledge updates. "
                "Keep claims grounded in returned source text."
            ),
        })
    if method == "ping":
        return _reply(request_id, {})
    if method == "tools/list":
        return _reply(request_id, {"tools": [_tool_def(t) for t in TOOLS.values()]})
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(name, str) or not name:
            return _error(request_id, -32602, "tools/call requires params.name")
        if not isinstance(arguments, dict):
            return _error(request_id, -32602, "tools/call params.arguments must be an object")
        # Optional private field for associating a direct knowledge proposal
        # with an existing Lodestar task without polluting public tool schemas.
        task_id = arguments.pop("_task_id", None)
        if task_id:
            ws.current_task_id = str(task_id)
        active_task = getattr(ws, "current_task_id", None)
        seq = getattr(ws, "_mcp_seq", 0) + 1
        ws._mcp_seq = seq
        if active_task:
            repo.add_trace_event(ws.conn, active_task, seq, "harness_tool_call",
                                 {"tool": name, "arguments": arguments})
        value = call_tool(ws, name, arguments)
        if active_task:
            repo.add_trace_event(ws.conn, active_task, seq + 1, "harness_tool_result",
                                 {"tool": name, "error": value.get("error") if isinstance(value, dict) else None})
            ws._mcp_seq = seq + 1
        return _reply(request_id, _text_result(value))
    if method == "notifications/initialized":
        return None
    return _error(request_id, -32601, f"Method not found: {method}")


def serve_stdio(cfg: Config | None = None) -> None:
    """Run the MCP JSON-RPC server over newline-delimited stdin/stdout."""
    ws = Workspace(cfg or load_config())
    task_id = os.getenv("LODESTAR_MCP_TASK_ID")
    if task_id:
        ws.current_task_id = task_id
        existing = repo.list_trace_events(ws.conn, task_id)
        ws._mcp_seq = max((int(e.get("seq") or 0) for e in existing), default=0)
    try:
        for line in sys.stdin:
            if not line.strip():
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError as exc:
                response = _error(None, -32700, "Parse error", str(exc))
            else:
                response = (_error(None, -32600, "Invalid Request")
                            if not isinstance(request, dict)
                            else handle_request(request, ws))
            if response is not None:
                sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                sys.stdout.flush()
    finally:
        ws.close()


__all__ = ["handle_request", "serve_stdio"]