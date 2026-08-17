"""ResearchAgent —— 唯一 Agent（Single Agent + Tools，PRD §16/§17）。

编排器是确定性代码：每一步先 Trace 再执行；LLM 只产内容（Plan/Query/Rerank/Assess/
Synthesis/Novelty），不决定工具图。这样最简单、可追踪、可回归。
"""
from __future__ import annotations

import sys
from typing import Optional

from lodestar import brief as brief_mod
from lodestar import prompts
from lodestar.agent import (assessor as assessor_mod, novelty as novelty_mod,
                                planner as planner_mod, queries as queries_mod,
                                reranker as reranker_mod, synthesizer as synthesizer_mod)
from lodestar import venue as venue_mod
from lodestar.llm import LLMClient, LLMError
from lodestar.memory import repo
from lodestar.tools.registry import call_tool
from lodestar.trace.recorder import Trace

# max_replans 已进 config.py（v0.1.10：1→2）
NO_EVIDENCE_BRIEF_TEMPLATE = (
    "# Research Brief — {goal}\n\n"
    "## 核心结论\n本次检索未能获得可用来源（候选 0 条）。可能原因：查询过窄、检索后端失败、"
    "网络不可用。建议换用更宽泛的表述，或检查网络后再试。\n\n"
    "## Open Questions\n- 需要重试或人工介入。\n"
)


class ResearchAgent:
    def __init__(self, ws, llm: Optional[LLMClient] = None, judge: Optional[LLMClient] = None,
                 interactive: Optional[bool] = None):
        self.ws = ws
        self.cfg = ws.config
        self.llm = llm or LLMClient(self.cfg)
        self.judge = judge or LLMClient(self.cfg, judge=True)
        self.interactive = sys.stdin.isatty() if interactive is None else interactive

    # ------------------------------------------------------------------
    def run(self, goal: str, apply_updates: Optional[bool] = None) -> dict:
        """执行一次 Research Task。

        apply_updates: True=直接应用 knowledge 更新；False=全部拒绝；
        None=交互式确认（非 tty 自动应用，供 eval/脚本）。
        """
        cfg = self.cfg
        ws = self.ws
        task_id = ws.new_task_id()
        ws.current_task_id = task_id
        trace = Trace(ws.conn, task_id, cfg.workspace_dir)
        trace.log("start", {"goal": goal, "llm_mode": cfg.llm_mode, "model": cfg.model})

        try:
            return self._run_inner(goal, task_id, trace, apply_updates)
        except LLMError as e:
            repo.finish_task(ws.conn, task_id, "", status="error", metrics={"error": str(e)})
            trace.log("error", {"error": str(e), "stage": "llm"})
            trace.dump_jsonl()
            return {"task_id": task_id, "goal": goal, "error": str(e), "status": "error"}

    # ------------------------------------------------------------------
    def _run_inner(self, goal, task_id, trace, apply_updates) -> dict:
        ws, cfg = self.ws, self.cfg

        # 1. Load Knowledge Context（PRD §2.1）
        knowledge_ctx = repo.search_concepts(ws.conn, goal, limit=10)
        trace.log("knowledge_context", {"goal": goal, "matched": [c["name"] for c in knowledge_ctx]})

        # 2. Plan（PRD §8.2）
        plan = planner_mod.plan(cfg, self.llm, goal, knowledge_ctx)
        trace.log("plan", plan)

        # 3. Query Rewrite / Expansion（PRD §8.3）
        queries = queries_mod.expand_queries(cfg, self.llm, goal, plan, knowledge_ctx)
        trace.log("queries", queries)
        repo.create_task(ws.conn, task_id, goal, plan, queries, cfg.llm_mode)

        # 4. Research Loop：Search → Dedup → Rerank → Read（PRD §17）
        candidates, searches = self._collect(queries, trace)
        sources = self._dedup(candidates)
        trace.log("sources_collected", {"candidates": len(candidates), "unique": len(sources),
                                        "searches": searches})
        # V1-R1：venue 元数据回填（Semantic Scholar），失败降级不阻断
        sources, venue_note = venue_mod.enrich_papers_venues(cfg, sources)
        trace.log("venue_enrich", {"note": venue_note,
                                   "papers": sum(1 for s in sources if s.get("source_type") == "paper"),
                                   "with_venue": sum(1 for s in sources if s.get("venue"))})
        for s in sources:
            repo.add_source(ws.conn, task_id, s)

        if not sources:
            brief_md = NO_EVIDENCE_BRIEF_TEMPLATE.format(goal=goal)
            metrics = {"queries": len(queries), "searches": searches, "candidates_collected": 0,
                       "unique_sources": 0, "sources_read": 0, "replans": 0}
            repo.finish_task(ws.conn, task_id, brief_md, "finished", metrics)
            trace.log("finish", {"metrics": metrics, "note": "无证据，Brief 为占位"})
            trace.dump_jsonl()
            return {"task_id": task_id, "goal": goal, "brief_md": brief_md, "sources": [],
                    "metrics": metrics, "updates": [], "workspace_dir": str(cfg.workspace_dir / task_id)}

        ranked = reranker_mod.rerank(cfg, self.llm, goal, plan["research_questions"], sources, knowledge_ctx)
        trace.log("rerank", [{"title": s["title"], "score": s.get("score"), "rank": s.get("rank"),
                              "reason": s.get("reason")} for s in ranked])
        id_by_title = {s["title"]: s["id"] for s in repo.list_sources(ws.conn, task_id)}
        for s in ranked[: cfg.max_deep_read_sources]:
            sid = id_by_title.get(s["title"])
            if sid:
                repo.update_source(ws.conn, sid, rank=s.get("rank"), reason=s.get("reason"))

        # V1-R2：config 开启全文时，Top N 论文来源读 PDF 全文（token 预算守护）
        full_text_count = cfg.full_text_max_sources if cfg.full_text_enabled else 0
        read_sources = self._deep_read(ranked, trace, full_text_count=full_text_count)
        for rs in read_sources:
            sid = id_by_title.get(rs["title"])
            if sid:
                repo.update_source(ws.conn, sid, read_depth=rs.get("read_depth", "none"))

        # 5. Assess（可有限 replan，受 max_agent_steps / max_search_queries 约束）
        evidence = self._evidence_summary(read_sources)
        assess = assessor_mod.assess(cfg, self.llm, goal, plan["research_questions"], evidence)
        trace.log("assess", assess)
        replans = 0
        while assess["decision"] == "replan" and replans < cfg.max_replans and searches < cfg.max_agent_steps:
            replans += 1
            extra_queries = self._gaps_to_queries(assess["gaps"] or [])
            trace.log("replan", {"extra_queries": extra_queries, "reason": assess["reason"]})
            extra, searches = self._collect(extra_queries, trace, searches=searches)
            seen = {s["dedup_key"] for s in sources}
            added = [s for s in self._dedup(extra) if s["dedup_key"] not in seen]
            if added:
                added, _ = venue_mod.enrich_papers_venues(cfg, added)  # replan 补搜同样回填 venue
            for s in added:
                repo.add_source(ws.conn, task_id, s)
                sources.append(s)
            if added:
                extra_ranked = reranker_mod.rerank(cfg, self.llm, goal, plan["research_questions"], added, knowledge_ctx)
                # V1-R2：assess 判定证据不足才补搜 → 补搜的 Top 1 来源读全文
                read_sources += self._deep_read(extra_ranked, trace, full_text_count=1)
            assess = assessor_mod.assess(cfg, self.llm, goal, plan["research_questions"], self._evidence_summary(read_sources))
            trace.log("assess", assess)

        # 6. Cross-source Synthesis（PRD §13）
        synthesis = synthesizer_mod.synthesize(cfg, self.llm, goal, plan["research_questions"],
                                               read_sources, knowledge_ctx)
        trace.log("synthesis", {"chars": len(synthesis)})

        # 7. Novelty Detection（PRD §12）
        novelty = novelty_mod.detect(cfg, self.llm, goal, synthesis, knowledge_ctx)
        trace.log("novelty", novelty)

        # 8. Knowledge Update Proposal（PRD §19：先提案、后确认）
        updates = self._build_updates(task_id, novelty, knowledge_ctx, read_sources)
        trace.log("knowledge_updates_proposed", updates)
        apply = self._decide_updates(updates, apply_updates)
        applied = self._apply_updates(updates, apply)
        trace.log("knowledge_updates_applied", applied)

        # 9. Brief + Finish
        metrics = {"queries": len(queries), "searches": searches,
                   "candidates_collected": len(candidates), "unique_sources": len(sources),
                   "sources_read": len(read_sources), "replans": replans}
        brief_md = brief_mod.render_brief(cfg, task_id, goal, plan, queries, sources, read_sources,
                                          synthesis, novelty, knowledge_ctx, assess, metrics)
        repo.finish_task(ws.conn, task_id, brief_md, "finished", metrics)
        trace.log("finish", {"metrics": metrics, "workspace": str(cfg.workspace_dir / task_id)})
        trace.dump_jsonl()
        out_dir = brief_mod.write_workspace(cfg.workspace_dir, task_id, brief_md, sources,
                                            cfg.workspace_dir / task_id / "trace.jsonl")

        if self.interactive:
            self._ask_feedback(task_id)

        return {"task_id": task_id, "goal": goal, "brief_md": brief_md, "sources": sources,
                "metrics": metrics, "updates": applied, "workspace_dir": str(out_dir)}

    # ------------------------------------------------------------------
    # 研究循环子步骤
    # ------------------------------------------------------------------
    def _collect(self, queries: list[dict], trace: Trace, searches: int = 0) -> tuple[list[dict], int]:
        """对每个 query 跑 search_papers + search_web，收集候选来源。"""
        ws, cfg = self.ws, self.cfg
        candidates: list[dict] = []
        for q in queries[: cfg.max_search_queries]:
            if searches >= cfg.max_agent_steps:
                break
            for tool in ("search_papers", "search_web"):
                params = {"query": q["text"]}
                trace.tool_call(tool, params)
                result = call_tool(ws, tool, params)
                trace.tool_result(tool, result)
                searches += 1
                if result.get("error"):
                    trace.log("tool_error", {"tool": tool, "error": result["error"]})
                    continue
                for s in result.get("sources", []):
                    s["query"] = q["text"]
                    candidates.append(s)
        return candidates, searches

    @staticmethod
    def _dedup(candidates: list[dict]) -> list[dict]:
        seen: set = set()
        out = []
        for s in candidates:
            key = s.get("dedup_key") or ""
            if not key:
                import re
                key = "title:" + re.sub(r"[^a-z0-9]+", "", (s.get("title") or "").lower())[:80]
            if key in seen:
                continue
            seen.add(key)
            out.append(s)
        return out

    def _deep_read(self, ranked: list[dict], trace: Trace, full_text_count: int = 0) -> list[dict]:
        """深度读取 Top N 来源（受 max_deep_read_sources 限制），带硬截断。

        V1-R2：full_text_count>0 时，前 N 个论文来源请求 PDF 全文（token 预算守护：
        默认 config 只给 Top 2，或 assess 证据不足补搜时给 1）。读不到全文优雅降级 abstract。
        """
        ws, cfg = self.ws, self.cfg
        read_sources = []
        remaining_full = full_text_count
        for s in ranked[: cfg.max_deep_read_sources]:
            is_paper = s.get("source_type") == "paper"
            want_full = is_paper and remaining_full > 0
            tool = "read_paper" if is_paper else "read_webpage"
            params = {"url": s["url"], "char_budget": cfg.read_char_budget}
            if want_full:
                params["full_text"] = True
            trace.tool_call(tool, params)
            result = call_tool(ws, tool, params)
            trace.tool_result(tool, {"title": result.get("title"), "truncated": result.get("truncated"),
                                     "error": result.get("error"), "full_text_ok": result.get("full_text_ok")})
            item = dict(s)
            if result.get("read_depth") == "full" and result.get("full_text_ok"):
                item["read_depth"] = "full"
                remaining_full -= 1
            else:
                item["read_depth"] = "abstract" if is_paper else "web"
            if result.get("error"):
                item["read_error"] = result["error"]
                item["content"] = f"（读取失败：{result['error']}）"
            else:
                item["content"] = result.get("text", "")
            read_sources.append(item)
        return read_sources

    @staticmethod
    def _evidence_summary(read_sources: list[dict]) -> str:
        lines = []
        for s in read_sources:
            head = (s.get("content") or "")[:600].replace("\n", " ")
            lines.append(f"- [{s['title']}]({s['url']}) read_depth={s.get('read_depth')} | {head}")
        return "\n".join(lines) or "（无已读来源）"

    def _gaps_to_queries(self, gaps: list[str]) -> list[dict]:
        """把 assess 标记的研究缺口转为检索 Query（LLM 提取关键词，比直接用 gap 文本更精准）。"""
        if not gaps:
            return []
        joined = "；".join(gaps[:3])
        try:
            text = self.llm.complete(
                "gap_queries",
                "# ROLE: gap_queries\n\n将以下研究缺口转化为 1-3 个英文检索 Query（用于 arXiv 搜索），"
                "每个 Query 用关键词拼接、去掉冗余修饰。只输出逐行 Query，每行一个。",
                f"缺口：{joined}",
                max_tokens=200,
            )
            lines = [l.strip() for l in text.strip().splitlines() if l.strip() and not l.strip().startswith("#")]
            return [{"text": l, "purpose": "assess 缺口补搜"} for l in lines[:3]] or [
                {"text": f"supplemental: {gaps[0][:60]}", "purpose": "assess 补搜（LLM 产出为空，回退原文本）"}
            ]
        except Exception:  # noqa: BLE001
            return [{"text": f"supplemental: {g[:60]}", "purpose": "assess 补搜（LLM 失败，回退）"} for g in gaps[:2]]

    # ------------------------------------------------------------------
    # Knowledge Update（HITL）
    # ------------------------------------------------------------------
    def _build_updates(self, task_id: str, novelty: dict, knowledge_ctx: list[dict],
                       read_sources: list[dict]) -> list[dict]:
        """从 novelty claims 生成 Knowledge Update 提案（确定性映射，PRD §19）。"""
        ws = self.ws
        known = {c["name"] for c in knowledge_ctx}
        evidence = [{"title": s["title"], "url": s["url"]} for s in read_sources[:5]]
        updates = []
        for claim in novelty.get("claims", []):
            concept = (claim.get("concept") or "").strip()
            level = claim.get("novelty")
            if not concept or level == "low":
                continue  # low = 重包装，不改状态
            existing = repo.get_concept(ws.conn, concept)
            action = "update" if (existing or concept in known) else "create"
            new_status = "known" if level == "high" else "partial"
            new_conf = "medium" if level == "high" else "low"
            proposal = {
                "old_status": existing["status"] if existing else None,
                "new_status": new_status,
                "old_confidence": existing["confidence"] if existing else None,
                "new_confidence": new_conf,
                "evidence": evidence,
                "reasoning": claim.get("reason") or "",
                "claim": claim.get("claim") or "",
                "novelty": level,
            }
            res = call_tool(ws, "update_knowledge_proposal",
                            {"concept": concept, "action": action, "proposal": proposal})
            updates.append({"concept": concept, "action": action, "novelty": level,
                            "update_id": res.get("update_id"), "proposal": proposal})
        return updates

    def _decide_updates(self, updates: list[dict], apply_updates: Optional[bool]) -> bool:
        if apply_updates is not None:
            return apply_updates
        if not self.interactive:
            return True  # 非交互（eval/脚本）：自动应用
        if not updates:
            return True
        print("\n" + "=" * 60)
        print("以下 Knowledge State 修改待确认（PRD §19：修改前必须确认）：")
        for u in updates:
            p = u["proposal"]
            old = f"{p.get('old_status')}/{p.get('old_confidence')}" if p.get("old_status") else "（无）"
            new = f"{p['new_status']}/{p['new_confidence']}"
            print(f"  [{u['action']}] {u['concept']}: {old} → {new}\n      依据: {u['proposal']['claim']}")
        ans = input("应用以上更新？[y/N] ").strip().lower()
        return ans in {"y", "yes"}

    def _apply_updates(self, updates: list[dict], apply: bool) -> list[dict]:
        ws = self.ws
        applied = []
        for u in updates:
            status = "applied" if apply else "rejected"
            if apply:
                p = u["proposal"]
                note = f"[研究笔记] {p.get('claim')}（novelty={u['novelty']}）—— {p.get('reasoning')}"
                repo.upsert_concept(ws.conn, u["concept"], status=p["new_status"],
                                    confidence=p["new_confidence"], append_note=note)
            if u.get("update_id"):
                repo.set_update_status(ws.conn, u["update_id"], status)
            applied.append({"concept": u["concept"], "action": u["action"], "status": status})
        return applied

    def _ask_feedback(self, task_id: str) -> None:
        """采集用户反馈（PRD 缺口 B2）。"""
        try:
            usefulness = input("\nBrief 有用性（0-5，回车跳过）: ").strip()
            best = input("最有价值的点（回车跳过）: ").strip()
            repeated = input("最重复/已知的点（回车跳过）: ").strip()
        except (EOFError, KeyboardInterrupt):
            return
        repo.add_feedback(self.ws.conn, task_id,
                          usefulness=int(usefulness) if usefulness.isdigit() else None,
                          best_item=best or None, repeated_item=repeated or None)
