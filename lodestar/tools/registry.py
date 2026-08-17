"""Tool 注册表（PRD §18：V0 控制在 6-8 个工具）。

每个工具：name / description / parameters(JSON schema 简化) / fn(ws, **params)。
调用统一走 call_tool：异常被捕获为 {"error": ...}，保证 Trace 可审计、Loop 不因单工具崩溃。
"""
from __future__ import annotations

import json
from typing import Callable

TOOLS: dict = {}


def register(name: str, description: str, fn: Callable, parameters: dict) -> None:
    TOOLS[name] = {"name": name, "description": description, "parameters": parameters, "fn": fn}


def describe_tools() -> str:
    lines = ["## 可用工具", ""]
    for t in TOOLS.values():
        params = ", ".join(f"{k}{'*' if p.get('required') else ''}:{p.get('type')}" for k, p in t["parameters"].items())
        lines.append(f"- `{t['name']}({params})` — {t['description']}")
    return "\n".join(lines)


def call_tool(ws, name: str, params: dict):
    """执行工具；任何异常都返回 {"error": ...} 而非抛出。"""
    tool = TOOLS.get(name)
    if tool is None:
        return {"error": f"未知工具: {name}"}
    try:
        return tool["fn"](ws, **params)
    except Exception as e:  # noqa: BLE001 —— 工具失败不打断 Loop
        return {"error": f"工具 {name} 执行失败: {e}", "tool": name, "params": params}
