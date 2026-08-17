"""Eval 指标（PRD §21/§22）。

两层设计（对应 PRD 缺口 A3）：
- 确定性指标：由 Trace / DB 数据直接算出，可复现、不依赖 LLM。
- 质量指标：Judge-LLM 按 rubric 打分（task_success / faithfulness / planning / novelty）。
  Judge 失败时该指标置 None，verdict 降级为 warn，绝不编造分数。
"""
from __future__ import annotations

import datetime as dt

from lodestar import prompts
from lodestar.eval.cases import GoldenCase


def _iso_seconds(s: str) -> float | None:
    try:
        return dt.datetime.fromisoformat(s).timestamp()
    except (ValueError, TypeError):
        return None


def compute_metrics(cfg, case: GoldenCase, task: dict, brief: str, sources: list[dict],
                    trace_events: list[dict], judge) -> dict:
    m = task.get("metrics") or {}
    metrics: dict = {"case_id": case.id}

    # ---- 确定性：来源质量 / 工具效率（来自 Trace 与 task.metrics）----
    metrics["unique_sources"] = int(m.get("unique_sources", len(sources)))
    metrics["candidates_collected"] = int(m.get("candidates_collected", 0))
    metrics["sources_read"] = int(m.get("sources_read", 0))
    metrics["queries"] = int(m.get("queries", 0))
    metrics["searches"] = int(m.get("searches", 0))
    metrics["replans"] = int(m.get("replans", 0))
    candidates = metrics["candidates_collected"]
    metrics["duplicate_dropped"] = max(0, candidates - metrics["unique_sources"])
    metrics["duplicate_rate"] = round(metrics["duplicate_dropped"] / candidates, 3) if candidates else 0.0
    t0, t1 = _iso_seconds(task.get("created_at")), _iso_seconds(task.get("finished_at"))
    metrics["elapsed_s"] = round(t1 - t0, 1) if (t0 and t1) else None
    # token 近似：trace 数据字段字符数（诚实标注为近似）
    approx_chars = sum(len(str(e.get("data") or "")) for e in trace_events)
    metrics["trace_chars_approx"] = approx_chars
    # 无意义重复搜索：只统计检索类工具，出现完全相同的 (tool, query) 次数（读工具不计入）
    seen_calls: set = set()
    dup_calls = 0
    for e in trace_events:
        if e.get("kind") != "tool_call":
            continue
        d = e.get("data") or {}
        tool = d.get("tool")
        if tool not in {"search_papers", "search_web"}:
            continue
        key = (tool, str(d.get("params", {}).get("query", "")))
        if key in seen_calls:
            dup_calls += 1
        seen_calls.add(key)
    metrics["duplicate_searches"] = dup_calls

    # ---- 确定性：覆盖度（brief 子串匹配，大小写不敏感）----
    brief_l = (brief or "").lower()
    covered = [c for c in case.expected.must_cover_concepts if c.lower() in brief_l]
    metrics["covered_concepts"] = covered
    metrics["missing_concepts"] = [c for c in case.expected.must_cover_concepts if c not in covered]
    total = max(1, len(case.expected.must_cover_concepts))
    metrics["coverage"] = round(len(covered) / total, 2)

    # ---- V1-R1：venue 解析覆盖（Semantic Scholar 回填是否生效）----
    papers = [s for s in sources if s.get("source_type") == "paper"]
    metrics["paper_sources"] = len(papers)
    metrics["venue_resolved"] = sum(1 for s in papers if s.get("venue"))
    metrics["venue_coverage"] = round(metrics["venue_resolved"] / len(papers), 2) if papers else None

    # ---- 质量指标：Judge-LLM ----
    if brief and sources:
        goal = case.goal
        metrics["task_success"] = _judge(judge, "judge_task_success",
                                         *prompts.judge_task_success_prompt(cfg, goal, brief))
        metrics["faithfulness"] = _judge(judge, "judge_faithfulness",
                                         *prompts.judge_faithfulness_prompt(cfg, goal, brief, sources))
        metrics["planning"] = _judge(judge, "judge_planning",
                                     *prompts.judge_planning_prompt(cfg, goal, task.get("plan") or {},
                                                                    task.get("queries") or [],
                                                                    metrics["searches"]))
        metrics["novelty"] = _judge(judge, "judge_novelty",
                                    *prompts.judge_novelty_quality_prompt(cfg, goal, brief, []))
    else:
        metrics["task_success"] = metrics["faithfulness"] = metrics["planning"] = metrics["novelty"] = None

    # ---- Verdict ----
    metrics["verdict"] = _decide_verdict(case, metrics)
    return metrics


def _judge(judge, role: str, system: str, user: str) -> int | None:
    try:
        data = judge.complete_json(role, system, user)
        score = data.get("score")
        return int(score) if isinstance(score, (int, float)) else None
    except Exception:  # noqa: BLE001
        return None


def _decide_verdict(case: GoldenCase, m: dict) -> str:
    judged = ["task_success", "faithfulness", "planning", "novelty"]
    if any(m.get(k) is None for k in judged):
        return "warn"  # Judge 未完成，不能给 pass/fail 的确定结论
    th = case.thresholds
    det_ok = m["unique_sources"] >= case.expected.min_sources and m["coverage"] >= th.get("coverage", 0.6)
    # V1-R1：若 case 显式要求 venue 覆盖率，纳入判定（缺省不启用，避免外部 API 波动影响）
    if "min_venue_coverage" in th and m.get("venue_coverage") is not None:
        det_ok = det_ok and m["venue_coverage"] >= th["min_venue_coverage"]
    qual_ok = m["task_success"] >= th.get("task_success", 3) and m["faithfulness"] >= th.get("faithfulness", 3)
    return "pass" if (det_ok and qual_ok) else "fail"
