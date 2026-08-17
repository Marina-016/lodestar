"""Lodestar CLI。用法见 README。"""
from __future__ import annotations

import argparse
import json
import sys

from lodestar import __version__
from lodestar.agent.loop import ResearchAgent
from lodestar.config import load_config
from lodestar.context import Workspace
from lodestar.eval.runner import run_all
from lodestar.memory import repo


def _make_config(args) -> "Config":
    cfg = load_config()
    if getattr(args, "mock", False):
        cfg.llm_mode = "mock"
    if getattr(args, "offline", False):
        cfg.search_mode = "mock"
    return cfg


# ----------------------------------------------------------------------
def cmd_research(args, cfg):
    ws = Workspace(cfg)
    # --yes = 自动化模式：跳过 Knowledge 确认与反馈输入
    interactive = (not args.yes) and sys.stdin.isatty()
    agent = ResearchAgent(ws, interactive=interactive)
    result = agent.run(args.goal, apply_updates=None if not args.yes else True)
    if result.get("error"):
        print(f"[error] {result['error']}", file=sys.stderr)
        sys.exit(1)
    print(result.get("brief_md", ""))
    print(f"\n---\n产物目录: {result.get('workspace_dir')}")
    print(f"task_id: {result['task_id']}")
    ws.close()


def cmd_eval(args, cfg):
    results = run_all(cfg, case_id=args.case)
    if not results:
        print("没有匹配的 golden case。")
        sys.exit(1)
    print(f"{'case_id':<24} {'verdict':<6} {'unique':<7} {'read':<5} {'coverage':<8} "
          f"{'task_success':<12} {'faithfulness':<12} {'searches':<8} {'dup_searches':<12} {'venue_cov':<8}")
    def _cell(v, w):
        return str(v) if v is not None else "-"

    for r in results:
        s = r["scores"]
        print(f"{r['case_id']:<24} {r['verdict']:<6} {_cell(s.get('unique_sources'),7):<7} "
              f"{_cell(s.get('sources_read'),5):<5} {_cell(s.get('coverage'),8):<8} "
              f"{_cell(s.get('task_success'),12):<12} {_cell(s.get('faithfulness'),12):<12} "
              f"{_cell(s.get('searches'),8):<8} {_cell(s.get('duplicate_searches'),12):<12} "
              f"{_cell(s.get('venue_coverage'),8):<8}")
        print(f"    task_id={r['task_id']}  missing={s.get('missing_concepts', [])}")


def cmd_knowledge(args, cfg):
    ws = Workspace(cfg)
    if args.action == "list":
        for c in repo.list_concepts(ws.conn):
            print(f"- {c['name']} [{c['status']}/{c['confidence']}] notes={len(c['notes'])}")
    elif args.action == "search":
        for c in repo.search_concepts(ws.conn, args.query):
            print(f"- {c['name']} [{c['status']}/{c['confidence']}] — {('；'.join(c['notes'][-2:]))[:120]}")
    elif args.action == "get":
        c = repo.get_concept(ws.conn, args.name)
        print(json.dumps(c, ensure_ascii=False, indent=2) if c else f"（无概念 {args.name}）")
    elif args.action == "seed":
        names = [n.strip() for n in args.names.split(",") if n.strip()]
        n = repo.seed_concepts(ws.conn, [{"name": nm, "status": args.status, "confidence": args.confidence}
                                         for nm in names])
        print(f"已种子 {n} 个概念：{', '.join(names)}（status={args.status}, confidence={args.confidence}）")
    elif args.action == "diff":
        updates = repo.list_knowledge_updates(ws.conn, task_id=args.task_id)
        if not updates:
            print(f"task {args.task_id} 无 knowledge 更新。")
        for u in updates:
            p = u["proposal"]
            print(f"[{u['status']}] id={u['id']} [{u['action']}] {u['concept']}: "
                  f"{p.get('old_status')}/{p.get('old_confidence')} → {p['new_status']}/{p['new_confidence']}")
            print(f"    依据: {p.get('claim')}（novelty={p.get('novelty')}）")
    elif args.action == "rollback":
        upd = next((u for u in repo.list_knowledge_updates(ws.conn, status="applied")
                    if u["id"] == int(args.update_id)), None)
        if upd is None:
            print(f"找不到 applied 的 update id={args.update_id}")
        else:
            p = upd["proposal"]
            if p.get("old_status"):
                repo.upsert_concept(ws.conn, upd["concept"], status=p["old_status"],
                                    confidence=p.get("old_confidence") or "low")
            repo.set_update_status(ws.conn, upd["id"], "rejected")
            print(f"已回滚 {upd['concept']} 到 {p.get('old_status')}/{p.get('old_confidence')}（update id={upd['id']} 标 rejected）")
    ws.close()


def cmd_trace(args, cfg):
    ws = Workspace(cfg)
    events = repo.list_trace_events(ws.conn, args.task_id)
    if not events:
        print(f"task {args.task_id} 无 trace。")
        return
    if args.json:
        for e in events:
            print(json.dumps(e, ensure_ascii=False))
    else:
        for e in events:
            data = e.get("data") or {}
            preview = json.dumps(data, ensure_ascii=False)[:160]
            print(f"[{e['seq']:>3}] {e['kind']:<26} {preview}")
    ws.close()


def cmd_feedback(args, cfg):
    ws = Workspace(cfg)
    rows = repo.list_feedback(ws.conn)
    if not rows:
        print("暂无 feedback。")
    for r in rows:
        print(f"task={r['task_id']} useful={r['usefulness']} best={r['best_item']!r} "
              f"repeated={r['repeated_item']!r} note={r['note']!r}")
    ws.close()


def cmd_build(args, cfg):
    """V3 种子：把 prompt 交给外部 coding agent CLI 执行（Claude Code / Codex）。"""
    from lodestar.build import get_executor
    try:
        ex = get_executor(args.executor)
    except (ValueError, RuntimeError) as e:
        print(f"[error] {e}")
        sys.exit(1)
    print(f"executor: {ex.name}  available={ex.available()}")
    if not ex.available():
        print("[error] 该 CLI 未安装或不可用")
        sys.exit(1)
    print(f"prompt: {args.prompt[:80]}{'…' if len(args.prompt) > 80 else ''}")
    r = ex.run(args.prompt, timeout=args.timeout)
    if not r.ok:
        print(f"[error] exit 非 0：{r.error[:300]}")
        sys.exit(1)
    print("--- 输出 ---")
    print(r.output[:4000])


# ----------------------------------------------------------------------
def main(argv=None):
    # Windows 控制台中文显示：stdout/stderr 统一 UTF-8
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:  # noqa: BLE001
                pass
    p = argparse.ArgumentParser(prog="lodestar", description="Lodestar — AI 前沿技术研究 Workspace Agent (V0)")
    p.add_argument("--version", action="version", version=f"lodestar {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("research", help="执行一次 Research Task")
    pr.add_argument("goal", help="研究目标（自然语言）")
    pr.add_argument("--yes", action="store_true", help="跳过 Knowledge 更新确认，直接应用")
    pr.add_argument("--mock", action="store_true", help="LLM 用离线夹具（不烧 token）")
    pr.add_argument("--offline", action="store_true", help="检索/读取也走离线夹具（全离线可复现）")
    pr.set_defaults(fn=cmd_research)

    pe = sub.add_parser("eval", help="跑 Golden Case 回归")
    pe.add_argument("--case", default=None, help="只跑指定 case id")
    pe.add_argument("--mock", action="store_true", help="LLM 用离线夹具")
    pe.add_argument("--offline", action="store_true", help="检索也走离线夹具")
    pe.set_defaults(fn=cmd_eval)

    pk = sub.add_parser("knowledge", help="Knowledge State 管理")
    ksub = pk.add_subparsers(dest="action", required=True)
    ksub.add_parser("list").set_defaults(fn=cmd_knowledge)
    ks = ksub.add_parser("search")
    ks.add_argument("query")
    ksub.add_parser("get").add_argument("name")
    kseed = ksub.add_parser("seed")
    kseed.add_argument("names", help="逗号分隔的概念名")
    kseed.add_argument("--status", default="known")
    kseed.add_argument("--confidence", default="high")
    kdiff = ksub.add_parser("diff")
    kdiff.add_argument("task_id")
    kroll = ksub.add_parser("rollback")
    kroll.add_argument("update_id")
    pk.set_defaults(fn=cmd_knowledge)

    pt = sub.add_parser("trace", help="查看某 task 的 Trace")
    pt.add_argument("task_id")
    pt.add_argument("--json", action="store_true")
    pt.set_defaults(fn=cmd_trace)

    pf = sub.add_parser("feedback", help="列出用户反馈")
    pf.set_defaults(fn=cmd_feedback)

    pb = sub.add_parser("build", help="V3 种子：调 coding agent CLI 执行 prompt（claude/codex）")
    pb.add_argument("prompt", help="要执行的指令")
    pb.add_argument("--executor", default="auto", help="claude | codex | auto")
    pb.add_argument("--timeout", type=int, default=300)
    pb.set_defaults(fn=cmd_build)

    args = p.parse_args(argv)
    cfg = _make_config(args)
    args.fn(args, cfg)


if __name__ == "__main__":
    main()
