from __future__ import annotations

import gc
import json
import tempfile
import time
import unittest
from pathlib import Path

from lodestar import experiment
from lodestar.config import Config
from lodestar.context import Workspace
from lodestar.demo import DEMO_CONCEPTS, seed_demo
from lodestar.mcp_server import handle_request
from lodestar.memory import repo
from lodestar.ui import _run_research


class LifecycleTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ws = Workspace(Config(llm_mode="mock", search_mode="mock",
                                   db_path=self.root / "lifecycle.db",
                                   workspace_dir=self.root / "workspace"))

    def tearDown(self):
        self.ws.close()
        for attempt in range(3):
            try:
                self.tmp.cleanup()
                break
            except OSError:
                if attempt == 2:
                    raise
                gc.collect()
                time.sleep(0.05)

    def test_memory_review_is_explicit_and_auditable(self):
        repo.upsert_concept(self.ws.conn, "Old Memory", "known", "medium", notes=["old claim"])
        self.ws.conn.execute("UPDATE concepts SET updated_at=? WHERE name=?", ("2020-01-01T00:00:00+00:00", "Old Memory"))
        self.ws.conn.commit()
        candidates = repo.list_memory_review_candidates(self.ws.conn, older_than_days=30)
        self.assertEqual(candidates[0]["name"], "Old Memory")
        self.assertEqual(candidates[0]["review_reason"], "stale")
        result = repo.record_memory_review(self.ws.conn, "Old Memory", "archive", "superseded")
        self.assertEqual(result["new_status"], "archived")
        self.assertEqual(repo.get_concept(self.ws.conn, "Old Memory")["status"], "archived")
        audits = repo.list_memory_reviews(self.ws.conn, "Old Memory")
        self.assertEqual(audits[0]["decision"], "archive")
        self.assertEqual(audits[0]["reason"], "superseded")

    def test_memory_review_mcp_tools_leave_a_trace(self):
        repo.upsert_concept(self.ws.conn, "Review Me", "needs_review", "low")
        repo.create_task(self.ws.conn, "memory-review-task", "review memory", {}, llm_mode="live")
        self.ws.current_task_id = "memory-review-task"
        reply = handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "review_memory_candidates", "arguments": {"older_than_days": 30}
        }}, self.ws)
        payload = json.loads(reply["result"]["content"][0]["text"])
        self.assertEqual(payload["count"], 1)
        handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "record_memory_review", "arguments": {
                "concept": "Review Me", "decision": "retain", "reason": "checked by user"
            }
        }}, self.ws)
        self.assertEqual(len(repo.list_memory_reviews(self.ws.conn, "Review Me")), 1)
        kinds = [event["kind"] for event in repo.list_trace_events(self.ws.conn, "memory-review-task")]
        self.assertEqual(kinds, ["harness_tool_call", "harness_tool_result", "harness_tool_call", "harness_tool_result"])

    def test_unimplemented_scaffold_remains_explicitly_inconclusive(self):
        exp_id = repo.add_experiment(self.ws.conn, "candidate improves score")
        project = experiment.scaffold_experiment(repo.get_experiment(self.ws.conn, exp_id), self.root / "experiments")
        repo.set_experiment_build(self.ws.conn, exp_id, "scaffolded", str(project))
        result = experiment.run_experiment(project)
        self.assertFalse(result["ok"])
        self.assertEqual(result["metrics"]["verdict"], "inconclusive")
        self.assertEqual(result["metrics"]["case_count"], 6)
        repo.record_experiment_run(self.ws.conn, exp_id, result)
        saved = repo.get_experiment(self.ws.conn, exp_id)
        self.assertEqual(saved["build_status"], "scaffolded")
        self.assertIsNone(saved["built_at"])
        self.assertTrue(saved["last_run_at"])
        self.assertTrue((project / "research_plan.md").is_file())
        self.assertTrue((project / "cases.json").is_file())

    def test_passing_experiment_requires_all_case_metrics(self):
        exp_id = repo.add_experiment(self.ws.conn, "candidate improves task success")
        project = experiment.scaffold_experiment(repo.get_experiment(self.ws.conn, exp_id), self.root / "experiments")
        baseline = '''def run(cases):
    return {"status": "complete", "results": [
        {"case_id": c["id"], "task_success": 0.60, "evidence_grounding": 0.80, "memory_safety": 1.00, "tool_efficiency": 0.70, "note": "baseline"}
        for c in cases
    ]}
'''
        candidate = '''def run(cases):
    return {"status": "complete", "results": [
        {"case_id": c["id"], "task_success": 0.72, "evidence_grounding": 0.82, "memory_safety": 1.00, "tool_efficiency": 0.71, "note": "candidate"}
        for c in cases
    ]}
'''
        (project / "baseline.py").write_text(baseline, encoding="utf-8")
        (project / "candidate.py").write_text(candidate, encoding="utf-8")
        result = experiment.run_experiment(project)
        self.assertTrue(result["ok"])
        self.assertEqual(result["metrics"]["verdict"], "pass")
        self.assertEqual(result["metrics"]["candidate"]["delta"]["task_success"], 0.12)
        repo.record_experiment_run(self.ws.conn, exp_id, result, str(project))
        saved = repo.get_experiment(self.ws.conn, exp_id)
        self.assertEqual(saved["build_status"], "built")
        self.assertTrue(saved["built_at"])

    def test_incomplete_metric_contract_fails(self):
        exp_id = repo.add_experiment(self.ws.conn, "incomplete metric contract")
        project = experiment.scaffold_experiment(repo.get_experiment(self.ws.conn, exp_id), self.root / "experiments")
        incomplete = '''def run(cases):
    return {"status": "complete", "results": [{"case_id": c["id"], "task_success": 0.8} for c in cases]}
'''
        (project / "baseline.py").write_text(incomplete, encoding="utf-8")
        (project / "candidate.py").write_text(incomplete, encoding="utf-8")
        result = experiment.run_experiment(project)
        self.assertFalse(result["ok"])
        self.assertEqual(result["metrics"]["verdict"], "fail")
        repo.record_experiment_run(self.ws.conn, exp_id, result, str(project))
        self.assertEqual(repo.get_experiment(self.ws.conn, exp_id)["build_status"], "failed")

    def test_demo_replay_returns_curated_sources_without_live_model(self):
        task_id = "demo-replay-task"
        repo.create_task(self.ws.conn, task_id, "Explain agent memory", {}, llm_mode="live")
        self.ws.close()  # replay opens its own workspace; avoid a second SQLite connection in this Windows cleanup test
        cfg = Config(db_path=self.root / "lifecycle.db", workspace_dir=self.root / "workspace", demo_replay=True)
        _run_research(task_id, "Explain agent memory", cfg)
        self.ws = Workspace(cfg)
        task = repo.get_task(self.ws.conn, task_id)
        self.assertEqual(task["status"], "finished")
        self.assertTrue(task["metrics"]["demo_replay"])
        self.assertGreaterEqual(len(repo.list_sources(self.ws.conn, task_id)), 3)
        self.assertEqual([event["kind"] for event in repo.list_trace_events(self.ws.conn, task_id)],
                         ["demo_replay_start", "demo_replay_sources", "project_context_search",
                          "memory_risk_assessment", "knowledge_updates_proposed", "demo_replay_finish"])
        self.assertEqual(len(repo.list_knowledge_updates(self.ws.conn, task_id, status="pending")), 1)
        self.assertEqual(repo.list_knowledge_updates(self.ws.conn, task_id, status="pending")[0]["concept"],
                         "Memory Trust Gate")

    def test_weekly_frontier_replay_is_a_selection_step(self):
        task_id = "weekly-frontier-task"
        goal = "\u672c\u5468 Agent \u6709\u4ec0\u4e48\u70ed\u70b9\uff1f"
        repo.create_task(self.ws.conn, task_id, goal, {}, llm_mode="live")
        self.ws.close()
        cfg = Config(db_path=self.root / "lifecycle.db", workspace_dir=self.root / "workspace", demo_replay=True)
        _run_research(task_id, goal, cfg)
        self.ws = Workspace(cfg)
        task = repo.get_task(self.ws.conn, task_id)
        self.assertEqual(task["status"], "finished")
        self.assertEqual(task["metrics"]["conversation_harness"], "demo_replay")
        sources = repo.list_sources(self.ws.conn, task_id)
        self.assertEqual(sources[0]["url"], "https://arxiv.org/abs/2608.20202")
        self.assertEqual(sources[2]["url"], "https://arxiv.org/abs/2608.19993")
        self.assertEqual(len(repo.list_knowledge_updates(self.ws.conn, task_id, status="pending")), 0)
        self.assertEqual([event["kind"] for event in repo.list_trace_events(self.ws.conn, task_id)],
                         ["demo_replay_start", "demo_replay_sources", "project_context_search",
                          "demo_replay_finish"])

    def test_demo_code_followup_keeps_topic_without_memory_update(self):
        task_id = "demo-code-followup-task"
        goal = "\u67e5\u770b\u8fd9\u6b21\u7814\u7a76\u547d\u4e2d\u7684 Lodestar \u4ee3\u7801"
        repo.create_task(self.ws.conn, task_id, goal, {}, llm_mode="live")
        self.ws.close()
        cfg = Config(db_path=self.root / "lifecycle.db", workspace_dir=self.root / "workspace", demo_replay=True)
        _run_research(task_id, goal, cfg, replay_topic="demo-ls-002", replay_action="code_context")
        self.ws = Workspace(cfg)
        task = repo.get_task(self.ws.conn, task_id)
        self.assertEqual(task["status"], "finished")
        self.assertEqual(task["metrics"]["demo_topic"], "demo-ls-002")
        self.assertEqual(task["metrics"]["demo_action"], "code_context")
        self.assertIn("\u4ee3\u7801\u8bc1\u636e\u89e3\u8bfb", task["brief_md"])
        self.assertEqual(repo.list_knowledge_updates(self.ws.conn, task_id, status="pending"), [])
        kinds = [event["kind"] for event in repo.list_trace_events(self.ws.conn, task_id)]
        self.assertIn("demo_replay_code_context", kinds)
        self.assertNotIn("knowledge_updates_proposed", kinds)

    def test_memory_replay_is_grounded_in_core_project_files(self):
        self.ws.close()
        cfg = Config(db_path=self.root / "lifecycle.db", workspace_dir=self.root / "workspace", demo_replay=True)
        seed_demo(cfg, clean=True)
        self.ws = Workspace(cfg)
        task_id = "project-grounded-memory-task"
        repo.create_task(self.ws.conn, task_id, "Explain Agent Memory", {}, llm_mode="live")
        self.ws.close()
        _run_research(task_id, "Explain Agent Memory", cfg)
        self.ws = Workspace(cfg)
        event = next(item for item in repo.list_trace_events(self.ws.conn, task_id)
                     if item["kind"] == "project_context_search")
        self.assertIn("lodestar/agent/loop.py", event["data"]["matches"])
        self.assertIn("lodestar/memory/repo.py", event["data"]["matches"])

    def test_trusted_memory_scaffold_uses_adversarial_cases(self):
        exp_id = repo.add_experiment(
            self.ws.conn,
            "Memory Trust Gate 能否降低 memory trap 与 false majority？",
        )
        project = experiment.scaffold_experiment(
            repo.get_experiment(self.ws.conn, exp_id), self.root / "experiments"
        )
        cases = json.loads((project / "cases.json").read_text(encoding="utf-8"))
        self.assertEqual(
            [case["id"] for case in cases],
            ["no-memory-control", "independent-helpful-memory", "relevant-misleading-memory",
             "correlated-false-majority", "stale-conflict", "gate-audit-and-cost"],
        )
        plan = (project / "research_plan.md").read_text(encoding="utf-8")
        self.assertIn("Trusted-memory diagnostics", plan)
        self.assertIn("source independence", plan)

    def test_clean_demo_reset_is_backed_up_and_minimal(self):
        repo.upsert_concept(self.ws.conn, "Noise Memory", "known", "high")
        repo.upsert_project(self.ws.conn, "Noise Project", status="active")
        repo.create_conversation(self.ws.conn, "noise-conversation", "Noise")
        self.ws.close()
        cfg = Config(db_path=self.root / "lifecycle.db", workspace_dir=self.root / "workspace")
        result = seed_demo(cfg, clean=True)
        self.ws = Workspace(cfg)
        self.assertTrue(Path(result["backup"]["database"]).is_file())
        self.assertEqual(len(repo.list_projects(self.ws.conn)), 1)
        self.assertEqual(len(repo.list_concepts(self.ws.conn)), len(DEMO_CONCEPTS))
        self.assertEqual(len(repo.list_conversations(self.ws.conn)), 0)
        self.assertEqual(self.ws.conn.execute("SELECT COUNT(*) FROM research_tasks").fetchone()[0], 4)
        experiments = repo.list_experiments(self.ws.conn)
        self.assertEqual(len(experiments), 4)
        self.assertNotIn("built", {item["build_status"] for item in experiments})
        project = repo.list_projects(self.ws.conn)[0]
        self.assertGreater(result["indexed_files"], 20)
        self.assertGreater(len(repo.list_project_documents(self.ws.conn, project["id"])), 20)


if __name__ == "__main__":
    unittest.main(verbosity=2)
