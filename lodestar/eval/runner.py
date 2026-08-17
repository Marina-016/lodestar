"""Eval Runner：跑 golden case → 出指标 → 落 eval_runs。

隔离性：eval 使用独立 db（data/eval_lodestar.db），
运行前对 case.seed_knowledge 幂等预置基线，保证 Novelty 判定可复现，且不污染用户真实 Knowledge State。
"""
from __future__ import annotations

from pathlib import Path

from lodestar.agent.loop import ResearchAgent
from lodestar.config import Config
from lodestar.eval.cases import GoldenCase, load_cases
from lodestar.eval.metrics import compute_metrics
from lodestar.llm import LLMClient
from lodestar.memory import repo
from lodestar.context import Workspace


def eval_workspace(cfg: Config) -> Workspace:
    """eval 专用隔离 workspace（独立 db）。"""
    eval_db = cfg.db_path.parent / "eval_lodestar.db"
    eval_cfg = Config(**{**cfg.__dict__, "db_path": eval_db})
    return Workspace(eval_cfg)


def run_case(ws: Workspace, case: GoldenCase) -> dict:
    repo.seed_concepts(ws.conn, case.seed_knowledge)  # 幂等：每次运行回到同一基线
    agent = ResearchAgent(ws, interactive=False)
    result = agent.run(case.goal, apply_updates=True)

    task = repo.get_task(ws.conn, result["task_id"])
    sources = repo.list_sources(ws.conn, result["task_id"])
    trace_events = repo.list_trace_events(ws.conn, result["task_id"])
    judge = LLMClient(ws.config, judge=True)
    scores = compute_metrics(ws.config, case, task or {}, result.get("brief_md", ""),
                             sources, trace_events, judge)
    verdict = scores.pop("verdict")
    repo.save_eval_run(ws.conn, case.id, result["task_id"], ws.config.llm_mode, scores, verdict)
    return {"case_id": case.id, "task_id": result["task_id"], "scores": scores,
            "verdict": verdict, "goal": case.goal, "workspace_dir": result.get("workspace_dir")}


def run_all(cfg: Config, case_id: str | None = None) -> list[dict]:
    ws = eval_workspace(cfg)
    cases = load_cases(cfg.cases_dir)
    if case_id:
        cases = [c for c in cases if c.id == case_id]
    results = [run_case(ws, c) for c in cases]
    ws.close()
    return results
