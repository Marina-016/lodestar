"""Lodestar 离线冒烟/回归测试（unittest，无第三方测试依赖）。

运行：python -m unittest tests.test_smoke -v    （或：python tests/test_smoke.py）
说明：llm_mode=mock + search_mode=mock，全离线、确定性、不烧 token。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lodestar.agent.loop import ResearchAgent
from lodestar.config import Config
from lodestar.context import Workspace
from lodestar.eval.cases import load_cases
from lodestar.eval.runner import eval_workspace, run_case
from lodestar.memory import repo

GOLDEN_GOAL = ("研究最近 Self-Evolving Skill / Self-Evolving Agent 有哪些值得关注的技术进展，"
               "理解核心技术路径，以及它和 Skill、Memory、Eval 的关系。")


class SmokeTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="rf_smoke_")
        self.cfg = Config(
            llm_mode="mock",
            search_mode="mock",
            db_path=Path(self.tmp) / "test.db",
            workspace_dir=Path(self.tmp) / "ws",
            cases_dir=ROOT / "lodestar" / "eval" / "cases",
        )

    def test_01_research_end_to_end_mock(self):
        ws = Workspace(self.cfg)
        try:
            agent = ResearchAgent(ws, interactive=False)
            res = agent.run(GOLDEN_GOAL, apply_updates=True)
            self.assertNotIn("error", res)

            task = repo.get_task(ws.conn, res["task_id"])
            self.assertEqual(task["status"], "finished")
            self.assertIn("研究简报", res["brief_md"])
            self.assertGreaterEqual(len(res["sources"]), 3, "应至少收集 3 个去重后来源")
            self.assertLessEqual(len(res["sources"]), res["metrics"]["candidates_collected"], "去重应生效")
            self.assertGreaterEqual(res["metrics"]["sources_read"], 3, "应深度阅读至少 3 个来源")

            # V1-R1：mock 夹具的论文来源必须全部带 venue
            paper_sources = [s for s in res["sources"] if s.get("source_type") == "paper"]
            self.assertTrue(paper_sources, "应有 paper 来源")
            self.assertTrue(all(s.get("venue") for s in paper_sources),
                            "mock 下所有 paper 来源都应回填 venue")

            # Trace 关键事件齐全（PRD §20，含 venue_enrich）
            events = repo.list_trace_events(ws.conn, res["task_id"])
            kinds = [e["kind"] for e in events]
            for needed in ["plan", "queries", "tool_call", "venue_enrich", "rerank", "synthesis",
                           "novelty", "knowledge_updates_proposed", "knowledge_updates_applied", "finish"]:
                self.assertIn(needed, kinds, f"trace 缺 {needed}")

            # Knowledge 更新已应用（PRD §19 流程）
            updates = repo.list_knowledge_updates(ws.conn, res["task_id"], status="applied")
            self.assertTrue(updates, "应有 applied 的 knowledge 更新")
            # 更新确实写入了 Knowledge State
            for u in updates:
                c = repo.get_concept(ws.conn, u["concept"])
                self.assertIsNotNone(c, f"概念 {u['concept']} 应已写入 Knowledge State")

            # 产物落盘
            wdir = Path(res["workspace_dir"])
            for f in ("brief.md", "sources.json", "trace.jsonl"):
                self.assertTrue((wdir / f).exists(), f"缺产物 {f}")
        finally:
            ws.close()

    @staticmethod
    def _find_case(case_id):
        for c in load_cases(ROOT / "lodestar" / "eval" / "cases"):
            if c.id == case_id:
                return c
        raise AssertionError(f"找不到 golden case {case_id}")

    def test_02_eval_golden_case_pass(self):
        ws = eval_workspace(self.cfg)
        try:
            case = self._find_case("self_evolving_agent")
            r = run_case(ws, case)
            self.assertEqual(r["case_id"], "self_evolving_agent")
            self.assertEqual(r["verdict"], "pass", f"scores={r['scores']}")
            self.assertGreaterEqual(r["scores"]["unique_sources"], case.expected.min_sources)
            self.assertGreaterEqual(r["scores"]["coverage"], case.thresholds["coverage"])
            self.assertGreaterEqual(r["scores"]["task_success"], case.thresholds["task_success"])
            self.assertGreaterEqual(r["scores"]["faithfulness"], case.thresholds["faithfulness"])
            # V1-R1：venue 覆盖率达标（mock 夹具应 1.0）
            self.assertGreaterEqual(r["scores"]["venue_coverage"], case.thresholds["min_venue_coverage"],
                                    "venue 覆盖率应达到 case 阈值")
        finally:
            ws.close()

    def test_03_knowledge_update_rejected_when_not_confirmed(self):
        """PRD §19：更新默认 pending；未确认（apply=False）应全部 rejected。"""
        ws = Workspace(self.cfg)
        try:
            agent = ResearchAgent(ws, interactive=False)
            res = agent.run(GOLDEN_GOAL, apply_updates=False)
            updates = repo.list_knowledge_updates(ws.conn, res["task_id"])
            self.assertTrue(updates)
            self.assertTrue(all(u["status"] == "rejected" for u in updates),
                            "apply=False 时应全部 rejected")
        finally:
            ws.close()

    def test_04_all_golden_cases_pass_offline(self):
        """全部 golden case（主题化 mock）离线回归：verdict=pass 且主题覆盖达标。"""
        ws = eval_workspace(self.cfg)
        try:
            cases = load_cases(self.cfg.cases_dir)
            self.assertGreaterEqual(len(cases), 5, "golden cases 应至少 5 个")
            for case in cases:
                r = run_case(ws, case)
                self.assertEqual(r["verdict"], "pass",
                                 f"{case.id}: {r['scores']}")
                self.assertGreaterEqual(r["scores"]["coverage"], case.thresholds["coverage"],
                                        f"{case.id} 主题覆盖不足")
                self.assertGreaterEqual(r["scores"]["unique_sources"], case.expected.min_sources,
                                        f"{case.id} 来源数不足")
        finally:
            ws.close()

    def test_05_full_text_path_offline(self):
        """V1-R2：开启全文时 Top N 论文来源 read_depth=full；关闭时全为摘要级。"""
        ft_cfg = Config(llm_mode="mock", search_mode="mock",
                        full_text_enabled=True, full_text_max_sources=2,
                        db_path=Path(self.tmp) / "ft.db",
                        workspace_dir=Path(self.tmp) / "ws_ft",
                        cases_dir=self.cfg.cases_dir)
        ws = Workspace(ft_cfg)
        try:
            agent = ResearchAgent(ws, interactive=False)
            res = agent.run(GOLDEN_GOAL, apply_updates=True)
            rows = repo.list_sources(ws.conn, res["task_id"])
            fulls = [s for s in rows if s.get("read_depth") == "full"]
            self.assertTrue(fulls, "开启全文后应有 read_depth=full 的来源")
            self.assertGreaterEqual(len(fulls), 1, "Top 2 论文来源应读全文")
            # 关闭全文时对照：无 full
            off_cfg = Config(llm_mode="mock", search_mode="mock", full_text_enabled=False,
                             db_path=Path(self.tmp) / "off.db",
                             workspace_dir=Path(self.tmp) / "ws_off",
                             cases_dir=self.cfg.cases_dir)
            ws2 = Workspace(off_cfg)
            res2 = ResearchAgent(ws2, interactive=False).run(GOLDEN_GOAL, apply_updates=True)
            rows2 = repo.list_sources(ws2.conn, res2["task_id"])
            self.assertFalse(any(s.get("read_depth") == "full" for s in rows2),
                             "关闭全文时不应有 full 来源")
            ws2.close()
        finally:
            ws.close()

    def test_06_build_executor_resolution(self):
        """V3 种子：executor 解析（不依赖 CLI 安装，仅测抽象层）。"""
        from lodestar.build import get_executor
        self.assertEqual(get_executor("claude").name, "claude")
        self.assertEqual(get_executor("codex").name, "codex")
        self.assertEqual(get_executor("auto").name, get_executor("auto").name)
        with self.assertRaises(ValueError):
            get_executor("bogus")

    def test_07_experiment_closed_loop_offline(self):
        """V3：Research→Experiment→Build 离线骨架 + 确定性 scaffold。"""
        from lodestar import experiment as experiment_mod
        # 提取机会
        brief = ("## Project Opportunities\n"
                 "- **可验证方向**：假设 A。验证方式：比较 baseline 与 candidate。\n"
                 "- 假设 B\n"
                 "---\n*Lodestar · footer*\n")
        ops = experiment_mod.extract_opportunities(brief)
        self.assertEqual(len(ops), 2, "应提取两条机会并跳过 --- 分隔线")
        self.assertIn("假设 A", ops[0])
        self.assertIn("假设 B", ops[1])
        # scaffold + eval 跑通
        exp = {"id": 99, "task_id": "fake", "hypothesis": ops[0],
               "description": "测试", "source_claim": ops[0]}
        project = experiment_mod.scaffold_experiment(exp, Path(self.tmp) / "exp_out")
        self.assertTrue((project / "eval.py").exists())
        self.assertTrue((project / "baseline.py").exists())
        self.assertTrue((project / "candidate.py").exists())
        import subprocess
        r = subprocess.run(["python", "eval.py"], capture_output=True, text=True,
                           cwd=str(project), timeout=30)
        self.assertEqual(r.returncode, 0, f"eval.py 应能跑通：{r.stderr}")

    def test_08_frontier_offline(self):
        """V1：Weekly AI Frontier —— mock LLM 返回 3 条建议。"""
        from lodestar import frontier as frontier_mod
        from lodestar.llm import LLMClient
        cfg2 = Config(llm_mode="mock", db_path=Path(self.tmp) / "fr.db",
                      workspace_dir=Path(self.tmp) / "ws_fr", cases_dir=self.cfg.cases_dir)
        llm = LLMClient(cfg2)
        report = frontier_mod.generate_frontier(cfg2, llm,
                                                knowledge_ctx=[{"name": "Memory", "status": "known"}],
                                                recent_tasks=[])
        self.assertEqual(len(report["suggestions"]), 3)
        for s in report["suggestions"]:
            self.assertTrue(s["topic"] and s["why"])
            self.assertIn(s["priority"], {"high", "medium", "low"})

    def test_09_ui_server(self):
        """Web UI：本地 server 起得来，health/首页/任务 API 响应。"""
        import os
        import threading
        from http.server import ThreadingHTTPServer
        import urllib.request
        os.environ["NO_PROXY"] = "127.0.0.1,localhost"
        os.environ["no_proxy"] = "127.0.0.1,localhost"
        from lodestar import ui as ui_mod
        cfg = Config(llm_mode="mock", search_mode="mock", db_path=Path(self.tmp) / "ui.db",
                     workspace_dir=Path(self.tmp) / "ws_ui", cases_dir=self.cfg.cases_dir)
        import lodestar.config as cfg_mod
        cfg_mod.load_config = lambda: cfg  # 让 ui 模块用测试 config（避免写主库）
        ui_mod.load_config = lambda: cfg
        server = ThreadingHTTPServer(("127.0.0.1", 0), ui_mod.Handler)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            h = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=10).read())
            self.assertEqual(h["ok"], True)
            html = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=10).read().decode("utf-8")
            self.assertIn("AI 研究搭档", html)
            self.assertIn("工作区", html)
            self.assertIn("new EventSource", html)
            self.assertNotIn(">Workspace<", html)
            self.assertTrue(html.lstrip().startswith("<!doctype html"),
                            "HTML 必须原样输出，不能被 JSON 转义")
            self.assertIn(".view{display:none}", html)
            self.assertNotIn("\\n\\n", html[:200])  # 防 JSON 化残留
            self.assertNotIn("RESEARCH TASKS", html)
            self.assertNotIn("task-launcher", html)
            self.assertIn("next-actions", html)
            self.assertIn("next-primary", html)
            self.assertIn("document.querySelectorAll('.next-actions')", html)
            self.assertNotIn("startSelectedDemoTask", html)
            self.assertIn("task.metrics&&task.metrics.demo_replay)return", html)
            self.assertIn("__lodestarDemo", html)
            self.assertIn("body.chat-mode", html)
            self.assertIn("duration=Math.min(14000", html)
            self.assertIn("if(m.task_id)await decorateResearch", html)
            self.assertIn("setTimeout(()=>scrollChat(true),80)", html)
            self.assertIn("const target=Math.max", html)
            self.assertIn("memoryConfirmed:false", html)
            self.assertIn("if(pending.length&&!demoState?.memoryConfirmed)return", html)
            self.assertIn("el.querySelector('.memory-card')?.remove()", html)
            self.assertNotIn("????????????", html)
            self.assertIn(".welcome .suggestions{display:none!important}", html)
            self.assertIn(".composer-foot{margin-top:3px", html)
            self.assertIn("function firstSentence", html)
            self.assertIn("firstSentence(t.goal", html)
            self.assertIn("font-weight:400", html)
            self.assertIn("el.innerHTML=md(text.slice(0,i));scrollChat();", html)
            self.assertIn("async function buildExperiment(id)", html)
            self.assertIn("/api/experiment/build", html)
            tasks = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/tasks", timeout=10).read())
            self.assertIsInstance(tasks, list)
            catalog = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/demo/tasks", timeout=10).read())
            self.assertEqual(len(catalog["tasks"]), 5)
            self.assertEqual(catalog["tasks"][0]["id"], "demo-frontier-weekly")
            self.assertIn("Memory", catalog["tasks"][1]["tags"])
            stream_task_id = "stream-smoke"
            ws = Workspace(cfg)
            try:
                repo.create_task(ws.conn, stream_task_id, "流式接口测试", {}, llm_mode="mock")
                repo.finish_task(ws.conn, stream_task_id, "# 测试结果", "finished", metrics={})
            finally:
                ws.close()
            stream = urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/task/{stream_task_id}/stream", timeout=10)
            self.assertEqual(stream.headers.get_content_type(), "text/event-stream")
            body_lines = []
            for line in stream:
                body_lines.append(line.decode("utf-8"))
                if '"status": "finished"' in body_lines[-1]:
                    break
            body = "".join(body_lines)
            stream.close()
            self.assertIn("event: snapshot", body)
            self.assertIn('"status": "finished"', body)
        finally:
            server.shutdown()
            server.server_close()

    def test_10_projects_relevance_offline(self):
        """Projects：登记 active 项目后，mock 研究的 Brief 应含项目关联映射。"""
        from lodestar.build import get_executor  # noqa: F401 (确认 import 链)
        ws = Workspace(self.cfg)
        try:
            repo.upsert_project(ws.conn, "test-agent-proj", url="https://github.com/x/test-agent-proj",
                                description="an agent project", tech_stack=["python", "agent", "llm"],
                                status="active")
            agent = ResearchAgent(ws, interactive=False)
            res = agent.run(GOLDEN_GOAL, apply_updates=True)
            self.assertIn("## 项目关联", res["brief_md"])
            self.assertIn("test-agent-proj", res["brief_md"], "机会应映射到 active 项目")
            rows = repo.list_projects(ws.conn)
            self.assertEqual(len(rows), 1)
        finally:
            ws.close()

    def test_11_project_relevance_score_is_explainable(self):
        from lodestar.relevance import score_project_relevance

        project = {
            "name": "Lodestar / Agent Research Lab",
            "description": "Research agent with trusted memory and auditable trace",
            "tech_stack": ["Python", "Agent", "Memory", "Eval"],
            "status": "active",
        }
        result = score_project_relevance(
            "Agent memory trace eval implementation gap", project, evidence_count=3
        )
        self.assertGreaterEqual(result["score"], 75)
        self.assertEqual(result["score"], sum(result["breakdown"].values()))
        self.assertEqual(result["level"], "高")
        self.assertEqual(
            set(result["breakdown"]),
            {"technology_stack", "project_context", "code_evidence", "active_status"},
        )

    def test_12_demo_signals_explain_concept_relation_and_pdf(self):
        from lodestar.demo import DEMO_PROJECTS, DEMO_TASKS, _brief

        brief = _brief(DEMO_TASKS[1], DEMO_PROJECTS[0], [{"path": "lodestar/memory/repo.py"}])
        self.assertEqual(brief.count("### "), 3)
        self.assertEqual(brief.count("**论文讲了什么**"), 3)
        self.assertEqual(brief.count("**关键发现**"), 3)
        self.assertEqual(brief.count("**概念**"), 3)
        self.assertEqual(brief.count("**与当前项目的关系**"), 3)
        self.assertIn("MemTrapBench: Benchmarking Cognitive Traps in LLM Memory Use", brief)
        self.assertEqual(brief.count("查看 PDF 原文"), 3)
        self.assertNotIn("## 关键来源", brief)


if __name__ == "__main__":
    unittest.main(verbosity=2)
