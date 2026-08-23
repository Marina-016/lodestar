from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lodestar.config import Config
from lodestar.context import Workspace
from lodestar.eval.harness import evaluate_harness_task
from lodestar.mcp_server import handle_request
from lodestar.memory import repo


class HarnessContractTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Workspace(Config(llm_mode="mock", search_mode="mock",
                                   db_path=Path(self.tmp.name) / "harness.db",
                                   workspace_dir=Path(self.tmp.name) / "workspace"))

    def tearDown(self):
        self.ws.close()
        self.tmp.cleanup()

    def _task(self, task_id="harness-task"):
        repo.create_task(self.ws.conn, task_id, "contract test", {}, llm_mode="live")
        return task_id

    def test_mcp_trace_is_paired_and_expected_tool_is_observed(self):
        task_id = self._task()
        self.ws.current_task_id = task_id
        reply = handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                "params": {"name": "search_knowledge", "arguments": {"query": "memory"}}}, self.ws)
        self.assertIn("result", reply)
        report = evaluate_harness_task(self.ws, task_id, ["search_knowledge"])
        self.assertEqual(report["verdict"], "pass")
        self.assertEqual(report["metrics"]["tool_calls"], 1)
        self.assertEqual(report["metrics"]["tool_results"], 1)

    def test_missing_result_fails_trace_contract(self):
        task_id = self._task()
        repo.add_trace_event(self.ws.conn, task_id, 1, "harness_tool_call", {"tool": "search_knowledge"})
        report = evaluate_harness_task(self.ws, task_id, ["search_knowledge"])
        self.assertEqual(report["verdict"], "fail")
        self.assertFalse(report["checks"]["trace_complete"])

    def test_failed_tool_result_fails_contract(self):
        task_id = self._task()
        repo.add_trace_event(self.ws.conn, task_id, 1, "harness_tool_call", {"tool": "search_knowledge"})
        repo.add_trace_event(self.ws.conn, task_id, 2, "harness_tool_result", {"tool": "search_knowledge", "error": "timeout"})
        report = evaluate_harness_task(self.ws, task_id, ["search_knowledge"])
        self.assertEqual(report["verdict"], "fail")
        self.assertEqual(report["metrics"]["tool_error_count"], 1)

    def test_memory_apply_requires_explicit_confirmation_trace(self):
        task_id = self._task()
        update_id = repo.add_knowledge_update(self.ws.conn, task_id, "Memory", "create",
                                              {"new_status": "known", "new_confidence": "medium", "claim": "test"})
        repo.set_update_status(self.ws.conn, update_id, "applied")
        self.assertEqual(evaluate_harness_task(self.ws, task_id)["verdict"], "fail")
        repo.add_trace_event(self.ws.conn, task_id, 1, "knowledge_updates_confirmed",
                             {"actor": "user", "concepts": ["Memory"]})
        self.assertEqual(evaluate_harness_task(self.ws, task_id)["verdict"], "pass")

    def test_ui_confirmation_writes_auditable_trace_event(self):
        from lodestar.ui import _apply_task_updates
        task_id = self._task()
        repo.add_knowledge_update(self.ws.conn, task_id, "Memory", "create",
                                  {"new_status": "known", "new_confidence": "medium", "claim": "test", "novelty": "low"})
        self.assertEqual(_apply_task_updates(self.ws, task_id), ["Memory"])
        kinds = [event["kind"] for event in repo.list_trace_events(self.ws.conn, task_id)]
        self.assertIn("knowledge_updates_confirmed", kinds)
        self.assertEqual(evaluate_harness_task(self.ws, task_id)["verdict"], "pass")


if __name__ == "__main__":
    unittest.main(verbosity=2)
