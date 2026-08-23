"""Trace Recorder：每个 Research Task 保留完整 Trace（PRD §20）。

- 事件序列落库（sqlite trace_events）。
- 结束时导出 workspace/<task_id>/trace.jsonl 供人工/工具审计。
- kind 枚举：start / plan / queries / tool_call / tool_result / sources_collected /
  rerank / read / assess / replan / synthesis / novelty / knowledge_updates_proposed /
  knowledge_updates_applied / feedback / finish / error
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from lodestar.memory import repo


class Trace:
    def __init__(self, conn: sqlite3.Connection, task_id: str, workspace_dir: Path):
        self.conn = conn
        self.task_id = task_id
        self.workspace_dir = workspace_dir
        existing = repo.list_trace_events(conn, task_id)
        self._seq = max((int(event.get("seq") or 0) for event in existing), default=0)

    def log(self, kind: str, data) -> int:
        self._seq += 1
        repo.add_trace_event(self.conn, self.task_id, self._seq, kind, data)
        return self._seq

    def tool_call(self, tool: str, params: dict):
        self.log("tool_call", {"tool": tool, "params": params})

    def tool_result(self, tool: str, result):
        self.log("tool_result", {"tool": tool, "result": result})

    def dump_jsonl(self) -> Path:
        """导出 trace 到 workspace/<task_id>/trace.jsonl，并返回路径。"""
        events = repo.list_trace_events(self.conn, self.task_id)
        out_dir = self.workspace_dir / self.task_id
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "trace.jsonl"
        with open(path, "w", encoding="utf-8") as f:
            for ev in events:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        return path
