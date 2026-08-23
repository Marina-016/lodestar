"""Deterministic contract checks for a Codex/MCP research trace."""
from __future__ import annotations

from collections import Counter
from typing import Any

from lodestar.memory import repo


def _event_data(event: dict) -> dict:
    data = event.get("data") or {}
    return data if isinstance(data, dict) else {}


def evaluate_harness_task(ws, task_id: str, expected_tools: list[str] | None = None) -> dict[str, Any]:
    """Evaluate observable Harness behavior without calling an LLM."""
    task = repo.get_task(ws.conn, task_id)
    if not task:
        raise ValueError(f"task not found: {task_id}")
    trace = repo.list_trace_events(ws.conn, task_id)
    updates = repo.list_knowledge_updates(ws.conn, task_id=task_id)
    expected = set(expected_tools or [])
    calls = [event for event in trace if event.get("kind") == "harness_tool_call"]
    results = [event for event in trace if event.get("kind") == "harness_tool_result"]
    called_tools = [str(_event_data(event).get("tool") or "") for event in calls]
    result_tools = [str(_event_data(event).get("tool") or "") for event in results]
    trace_complete = Counter(called_tools) == Counter(result_tools)
    tool_errors = [{"tool": _event_data(event).get("tool"), "error": _event_data(event).get("error")}
                   for event in results if _event_data(event).get("error")]
    missing_tools = sorted(expected - set(called_tools))
    fallback_events = [event for event in trace if event.get("kind") == "harness_fallback"]
    confirmations = [event for event in trace if event.get("kind") == "knowledge_updates_confirmed"]
    applied_updates = [update for update in updates if update.get("status") == "applied"]
    memory_safe = not applied_updates or bool(confirmations)
    checks = {
        "trace_complete": trace_complete,
        "expected_tools_called": not missing_tools,
        "tool_results_successful": not tool_errors,
        "memory_confirmation_required": memory_safe,
        "fallback_visible": not fallback_events or bool(fallback_events),
    }
    failures = []
    if not trace_complete:
        failures.append("MCP tool calls and results are not paired.")
    if missing_tools:
        failures.append("Expected tools were not called: " + ", ".join(missing_tools))
    if tool_errors:
        failures.append("One or more MCP tool calls returned an error.")
    if not memory_safe:
        failures.append("Knowledge was applied without a user-confirmation trace event.")
    verdict = "fail" if failures else ("attention" if fallback_events else "pass")
    return {
        "task_id": task_id, "verdict": verdict, "checks": checks,
        "metrics": {"tool_calls": len(calls), "tool_results": len(results),
                    "trace_completeness": 1.0 if trace_complete else 0.0,
                    "expected_tool_coverage": 1.0 if not missing_tools else 0.0,
                    "tool_error_count": len(tool_errors),
                    "applied_memory_updates": len(applied_updates),
                    "fallback_count": len(fallback_events)},
        "called_tools": called_tools, "missing_tools": missing_tools,
        "tool_errors": tool_errors, "failures": failures,
    }
