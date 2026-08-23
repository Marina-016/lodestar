from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lodestar.config import Config
from lodestar.context import Workspace
from lodestar.mcp_server import handle_request
from lodestar.memory import repo
from lodestar.project_index import index_local_project


class ProjectContextTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ws = Workspace(Config(llm_mode="mock", search_mode="mock",
                                   db_path=self.root / "context.db",
                                   workspace_dir=self.root / "workspace"))
        self.project_id = repo.upsert_project(
            self.ws.conn, "lodestar-demo", url="https://github.com/example/lodestar",
            description="A test project", status="active",
        )
        self.source = self.root / "source"
        self.source.mkdir()
        (self.source / "README.md").write_text("# Lodestar\nThe memory trace is confirmed by a user action.\n", encoding="utf-8")
        (self.source / "memory.py").write_text(
            "def confirm_memory(trace):\n    trace.append('knowledge_updates_confirmed')\n", encoding="utf-8")
        (self.source / ".env").write_text("SECRET=must-not-be-indexed\n", encoding="utf-8")
        hidden = self.source / "node_modules"
        hidden.mkdir()
        (hidden / "ignored.js").write_text("must-not-be-indexed", encoding="utf-8")
        self.docs = index_local_project(self.source)
        repo.replace_project_documents(self.ws.conn, self.project_id, self.docs)

    def tearDown(self):
        self.ws.close()
        self.tmp.cleanup()

    def test_local_index_is_bounded_and_excludes_hidden_or_generated_files(self):
        self.assertEqual({doc["path"] for doc in self.docs}, {"README.md", "memory.py"})
        stored = repo.list_project_documents(self.ws.conn, self.project_id)
        self.assertEqual(len(stored), 2)

    def test_search_returns_grounded_file_excerpt(self):
        matches = repo.search_project_documents(self.ws.conn, "knowledge updates confirmed", self.project_id)
        self.assertTrue(matches)
        self.assertEqual(matches[0]["path"], "memory.py")
        self.assertIn("knowledge_updates_confirmed", matches[0]["excerpt"])

    def test_mcp_search_and_read_are_traceable(self):
        task_id = "project-context-task"
        repo.create_task(self.ws.conn, task_id, "ground a project answer", {}, llm_mode="live")
        self.ws.current_task_id = task_id
        reply = handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "search_project_context", "arguments": {"query": "knowledge_updates_confirmed", "project": str(self.project_id)}
        }}, self.ws)
        payload = json.loads(reply["result"]["content"][0]["text"])
        self.assertEqual(payload["count"], 1)
        document_id = payload["matches"][0]["id"]
        read = handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "read_project_file", "arguments": {"document_id": document_id}
        }}, self.ws)
        self.assertIn("knowledge_updates_confirmed", read["result"]["content"][0]["text"])
        kinds = [event["kind"] for event in repo.list_trace_events(self.ws.conn, task_id)]
        self.assertEqual(kinds, ["harness_tool_call", "harness_tool_result", "harness_tool_call", "harness_tool_result"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
