"""Lodestar CLI。用法见 README。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from lodestar import __version__, experiment as experiment_mod, frontier as frontier_mod
from lodestar.agent.loop import ResearchAgent
from lodestar.config import load_config
from lodestar.context import Workspace
from lodestar.eval.runner import run_all
from lodestar.llm import LLMClient
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


def _make_build_executor(cfg, name: str):
    """构造 build executor（codex 走网关/保险，claude 兜底）。"""
    if name == "codex":
        from lodestar.build.codex import CodexExecutor
        return CodexExecutor(
            model=cfg.codex_model,
            provider=cfg.codex_provider_name if cfg.codex_base_url else None,
            base_url=cfg.codex_base_url or None,
            require_gateway=cfg.codex_require_gateway,
        )
    from lodestar.build import get_executor
    return get_executor(name)


def cmd_build(args, cfg):
    """V3：把 prompt 交给外部 coding agent CLI 执行（默认 codex，可切 claude）。"""
    name = args.executor or cfg.build_executor
    try:
        ex = _make_build_executor(cfg, name)
    except (ValueError, RuntimeError) as e:
        print(f"[error] {e}")
        sys.exit(1)
    print(f"executor: {ex.name}  available={ex.available()}"
          + (f"  → 网关 {cfg.codex_base_url}" if name == "codex" and cfg.codex_base_url else ""))
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


def _cmd_ui(args, cfg):
    from lodestar.ui import serve
    serve(port=args.port, open_browser=not args.no_browser)

def cmd_demo(args, cfg):
    from lodestar.demo import seed_demo
    result = seed_demo(cfg)
    print(json.dumps(result, ensure_ascii=False))


def cmd_mcp(args, cfg):
    """Expose Lodestar's registered tools to an external agent harness."""
    from lodestar.mcp_server import serve_stdio
    serve_stdio(cfg)

def cmd_project(args, cfg):
    """Projects：GitHub 摄入 + 进行中状态。"""
    ws = Workspace(cfg)
    try:
        if args.action == "list":
            rows = repo.list_projects(ws.conn)
            if not rows:
                print("暂无项目。用 `lodestar project add <github_url>` 登记。")
            for p in rows:
                print(f"#{p['id']} [{p['status']:<7}] {p['name']} — 技术栈: {','.join(p['tech_stack'][:6]) or '-'}")
            return
        if args.action == "add":
            from lodestar.project import ingest_github
            for url in args.url:
                try:
                    info = ingest_github(url)
                except Exception as e:  # noqa: BLE001
                    print(f"[error] 摄入失败 {url}：{e}")
                    continue
                pid = repo.upsert_project(ws.conn, info["name"], url=info["url"],
                                          description=info.get("description"),
                                          tech_stack=info.get("tech_stack"),
                                          status=args.status)
                print(f"已登记项目 #{pid}：{info['name']}")
                print(f"  描述: {(info.get('description') or '')[:80]}")
                print(f"  技术栈: {', '.join(info.get('tech_stack') or []) or '-'}（最近 push: {info.get('last_push')}）")
                print(f"  状态: {args.status}")
            return
        if args.action == "status":
            repo.set_project_status(ws.conn, int(args.id), args.status)
            print(f"项目 #{args.id} 状态 → {args.status}")
            return
    finally:
        ws.close()


def cmd_frontier(args, cfg):
    """V1：Weekly AI Frontier Research —— 基于 Knowledge State 推荐本周该研究什么。"""
    ws = Workspace(cfg)
    try:
        knowledge_ctx = repo.list_concepts(ws.conn)
        recent_tasks = [dict(r) for r in ws.conn.execute(
            "SELECT goal, created_at FROM research_tasks WHERE status='finished' ORDER BY created_at DESC LIMIT 5"
        ).fetchall()]
        projects = repo.list_projects(ws.conn, status="active")
        llm = LLMClient(cfg)
        report = frontier_mod.generate_frontier(cfg, llm, knowledge_ctx, recent_tasks, projects)
        print(f"# Weekly AI Frontier Research\n")
        for i, s in enumerate(report["suggestions"], 1):
            print(f"## {i}. {s['topic']}")
            print(f"**优先级**：{s['priority']}")
            if s.get("related_projects"):
                print(f"**相关项目**：{'、'.join(s['related_projects'])}")
            print(f"\n{s['why']}\n")
        if args.save:
            p = cfg.workspace_dir / f"frontier_{ws.new_task_id()}.md"
            p.write_text("\n\n".join(
                f"# {s['topic']}\n**优先级**：{s['priority']}\n\n{s['why']}"
                for s in report["suggestions"]
            ), encoding="utf-8")
            print(f"已保存：{p}")
    finally:
        ws.close()


def cmd_experiment(args, cfg):
    """V3：Research → Experiment → Build。"""
    ws = Workspace(cfg)
    try:
        if args.action == "list":
            rows = repo.list_experiments(ws.conn)
            if not rows:
                print("暂无 experiment。")
            for e in rows:
                print(f"#{e['id']} [{e['build_status']:<7}] task={e.get('task_id') or '-'}  {e['hypothesis'][:60]}")
            return
        if args.action == "save":
            task = repo.get_task(ws.conn, args.task_id)
            if not task:
                print(f"[error] 找不到 task {args.task_id}")
                sys.exit(1)
            hyp = args.hypothesis
            if not hyp:
                opts = experiment_mod.extract_opportunities(task.get("brief_md") or "")
                if not opts:
                    print(f"[error] task {args.task_id} 的 brief 里没有 Project Opportunities 可提取")
                    sys.exit(1)
                idx = (args.pick or 1) - 1
                if not (0 <= idx < len(opts)):
                    print(f"[error] --pick {args.pick} 越界（共 {len(opts)} 条）")
                    sys.exit(1)
                hyp = opts[idx]
                print(f"从 Project Opportunities 提取（#{(idx + 1)}/{len(opts)}）：{hyp[:80]}")
            exp_id = repo.add_experiment(ws.conn, hyp, task_id=args.task_id,
                                        description=args.description)
            print(f"已保存 Experiment #{exp_id}（draft）：{hyp[:60]}")
            return
        if args.action == "build":
            exp = repo.get_experiment(ws.conn, args.exp_id)
            if not exp:
                print(f"[error] 找不到 Experiment #{args.exp_id}")
                sys.exit(1)
            out_dir = Path(args.out)
            if args.scaffold_only:
                project = experiment_mod.scaffold_experiment(exp, out_dir)
                repo.set_experiment_build(ws.conn, exp["id"], "built", str(project))
                print(f"[scaffold-only] 已生成：{project}")
                return
            try:
                ex = _make_build_executor(cfg, args.executor or cfg.build_executor)
            except (ValueError, RuntimeError) as e:
                print(f"[error] {e}")
                sys.exit(1)
            if not ex.available():
                print(f"[error] executor {ex.name} 不可用")
                sys.exit(1)
            repo.set_experiment_build(ws.conn, exp["id"], "building")
            project, result = experiment_mod.build_experiment(exp, out_dir, ex, timeout=args.timeout)
            status = "built" if result.ok else "failed"
            repo.set_experiment_build(ws.conn, exp["id"], status, str(project))
            print(f"[build] {'成功' if result.ok else '失败'}：{project}")
            if not result.ok:
                print(f"[error] {result.error[:400]}")
                sys.exit(1)
            print(result.output[:1500])
    finally:
        ws.close()


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

    pb = sub.add_parser("build", help="V3：调 coding agent CLI 执行 prompt（默认 codex，可切 claude）")
    pb.add_argument("prompt", help="要执行的指令")
    pb.add_argument("--executor", default=None, help="claude | codex | auto（缺省用 config.build_executor）")
    pb.add_argument("--timeout", type=int, default=300)
    pb.set_defaults(fn=cmd_build)

    pf = sub.add_parser("frontier", help="V1：Weekly AI Frontier —— 基于 Knowledge State 推荐本周该研究什么")
    pf.add_argument("--save", action="store_true", help="保存报告到 workspace/")
    pf.add_argument("--mock", action="store_true", help="LLM 用离线夹具")
    pf.set_defaults(fn=cmd_frontier)

    pui = sub.add_parser("ui", help="本地 Web UI（零依赖，默认 http://127.0.0.1:8123）")
    pui.add_argument("--port", type=int, default=8123)
    pui.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    pui.set_defaults(fn=lambda a, c: _cmd_ui(a, c))

    pd = sub.add_parser("demo", help="准备录屏用的 Lodestar 示例数据")
    dsub = pd.add_subparsers(dest="action", required=True)
    dsub.add_parser("seed", help="向当前 workspace 幂等写入示例数据").set_defaults(fn=cmd_demo)

    pmcp = sub.add_parser("mcp", help="Expose Lodestar tools over MCP stdio")
    pmcp.set_defaults(fn=cmd_mcp)
    pj = sub.add_parser("project", help="Projects：GitHub 摄入 + 进行中状态")
    pjsub = pj.add_subparsers(dest="action", required=True)
    pjsub.add_parser("list").set_defaults(fn=cmd_project)
    pja = pjsub.add_parser("add")
    pja.add_argument("url", nargs="+", help="一个或多个 GitHub 仓库链接")
    pja.add_argument("--status", default="active", help="active|paused|archived|idea")
    pjs = pjsub.add_parser("status")
    pjs.add_argument("id", type=int)
    pjs.add_argument("status", choices=["active", "paused", "archived", "idea"])
    pj.set_defaults(fn=cmd_project)

    pe = sub.add_parser("experiment", help="V3：Research→Experiment→Build")
    exsub = pe.add_subparsers(dest="action", required=True)
    exsub.add_parser("list").set_defaults(fn=cmd_experiment)
    es = exsub.add_parser("save", help="从 task 的 Project Opportunities 存一个实验")
    es.add_argument("task_id")
    es.add_argument("--pick", type=int, default=None, help="选第几条机会（从 1 起；缺省取第一条）")
    es.add_argument("--hypothesis", default=None, help="自定义假设（不填则从 brief 提取）")
    es.add_argument("--description", default=None)
    eb = exsub.add_parser("build", help="scaffold + coding agent 实现实验")
    eb.add_argument("exp_id", type=int)
    eb.add_argument("--out", default="experiments", help="输出根目录（默认 experiments/）")
    eb.add_argument("--executor", default=None, help="codex | claude（缺省用 config）")
    eb.add_argument("--timeout", type=int, default=300)
    eb.add_argument("--scaffold-only", action="store_true", help="只生成确定性骨架，不调 coding agent")
    pe.set_defaults(fn=cmd_experiment)

    args = p.parse_args(argv)
    cfg = _make_config(args)
    args.fn(args, cfg)


if __name__ == "__main__":
    main()
