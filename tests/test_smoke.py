"""Lodestar 离线冒烟/回归测试（unittest，无第三方测试依赖）。

运行：python -m unittest tests.test_smoke -v    （或：python tests/test_smoke.py）
说明：llm_mode=mock + search_mode=mock，全离线、确定性、不烧 token。
"""
from __future__ import annotations

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
            self.assertIn("Research Brief", res["brief_md"])
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
