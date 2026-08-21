"""Lodestar Web UI —— 本地单页仪表盘（Python stdlib，零新依赖）。

用法：python -m lodestar ui [--port 8123]
Tab：研究 / 选题（Weekly AI Frontier）/ 知识库 / 历史。
研究在后台线程运行，前端轮询状态；知识更新走「待应用 → 一键应用」的 HITL。
"""
from __future__ import annotations

import json
import threading
import webbrowser
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from pathlib import Path

from lodestar.agent.loop import ResearchAgent
from lodestar.config import load_config
from lodestar.context import Workspace
from lodestar.experiment import extract_opportunities, scaffold_experiment
from lodestar.frontier import generate_frontier
from lodestar.llm import LLMClient
from lodestar.memory import repo
from lodestar.quiz import evaluate_answer as quiz_evaluate
from lodestar.quiz import generate_question as quiz_question

_cfg = None
_runners: dict = {}  # task_id -> Thread


def _ws() -> Workspace:
    return Workspace(load_config())


def _concepts_text(concepts: list[dict]) -> str:
    if not concepts:
        return "（空）"
    return "；".join(f"{c['name']}[{c.get('status','?')}/{c.get('confidence','?')}]" for c in concepts[:30])


# ----------------------------------------------------------------------
# 后台研究
# ----------------------------------------------------------------------
def _append_conversation_result(conversation_id: str, task_id: str, cfg) -> None:
    if not conversation_id:
        return
    ws = Workspace(cfg)
    try:
        task = repo.get_task(ws.conn, task_id)
        if not task or task.get("status") != "finished":
            return
        updates = repo.list_knowledge_updates(ws.conn, task_id=task_id)
        sources = repo.list_sources(ws.conn, task_id)
        repo.add_message(
            ws.conn, conversation_id, "assistant", task.get("brief_md") or "",
            kind="research", task_id=task_id,
            metadata={"source_count": len(sources), "pending_updates": len([u for u in updates if u["status"] == "pending"])},
        )
    finally:
        ws.close()


def _run_research(task_id: str, goal: str, cfg, conversation_id: str | None = None) -> None:
    ws = Workspace(cfg)
    try:
        try:
            result = ResearchAgent(ws, interactive=False).run(goal, apply_updates="pending", task_id=task_id)
        except Exception as e:  # noqa: BLE001
            result = {"error": str(e), "status": "error"}
        if result.get("error") or result.get("status") == "error":
            _degrade_to_mock(task_id, goal, result.get("error") or "未知错误")
        _append_conversation_result(conversation_id, task_id, cfg)
    finally:
        ws.close()
        _runners.pop(task_id, None)


def _degrade_to_mock(task_id: str, goal: str, error: str) -> None:
    try:
        mock_cfg = load_config()
        mock_cfg.llm_mode = "mock"
        mock_cfg.search_mode = "mock"
        ws = Workspace(mock_cfg)
        try:
            ResearchAgent(ws, interactive=False).run(goal, apply_updates="pending", task_id=task_id)
            brief = (repo.get_task(ws.conn, task_id) or {}).get("brief_md") or ""
            repo.finish_task(ws.conn, task_id,
                             f"> ⚠️ 实时研究失败，以下为示例数据（mock 降级）。原因：{error}\n\n" + brief,
                             "finished", metrics={"degraded": True, "error": error})
        finally:
            ws.close()
    except Exception:  # noqa: BLE001
        pass


def _conversation_goal(ws: Workspace, conversation_id: str, content: str) -> str:
    history = repo.list_messages(ws.conn, conversation_id, limit=12)
    context = []
    for msg in history[-8:]:
        if msg.get("content"):
            body = msg["content"]
            if len(body) > 1200:
                body = body[:1200] + "…"
            context.append(f"{msg['role']}: {body}")
    if not context:
        return content
    return content + "\n\n## 对话上下文（仅用于理解当前问题）\n" + "\n\n".join(context)


def _start_research(goal: str, conversation_id: str | None = None) -> str:
    cfg = load_config()
    ws = Workspace(cfg)
    try:
        task_id = uuid.uuid4().hex[:12]
        repo.create_task(ws.conn, task_id, goal, {}, llm_mode=cfg.llm_mode, conversation_id=conversation_id)
        t = threading.Thread(target=_run_research, args=(task_id, goal, cfg, conversation_id), daemon=True)
        _runners[task_id] = t
        t.start()
        return task_id
    finally:
        ws.close()


def _apply_task_updates(ws: Workspace, task_id: str, want_ids: set[int] | None = None) -> list[str]:
    pend = repo.list_knowledge_updates(ws.conn, task_id=task_id, status="pending")
    applied = []
    for u in pend:
        if want_ids and u["id"] not in want_ids:
            continue
        p = u["proposal"]
        repo.upsert_concept(ws.conn, u["concept"], status=p["new_status"],
                            confidence=p["new_confidence"],
                            append_note=f"{p.get('claim')}（novelty={p.get('novelty')}）")
        repo.set_update_status(ws.conn, u["id"], "applied")
        applied.append(u["concept"])
    return applied


# ----------------------------------------------------------------------
# HTTP
# ----------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 静默
        pass

    def _send(self, code: int, obj, content_type="application/json"):
        if content_type.startswith("text/"):  # HTML 等直接发原文，不 JSON 化（否则浏览器收到转义字符串）
            body = obj.encode("utf-8") if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False).encode("utf-8")
        else:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n).decode("utf-8")) if n else {}

    # ---- routes ----
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            return self._send(200, PAGE_HTML, "text/html; charset=utf-8")
        if path == "/api/health":
            return self._send(200, {"ok": True, "version": __import__("lodestar").__version__})
        if path == "/api/conversations":
            ws = _ws()
            try:
                return self._send(200, repo.list_conversations(ws.conn))
            finally:
                ws.close()
        if path.startswith("/api/conversation/"):
            conversation_id = path.split("/")[3]
            ws = _ws()
            try:
                conversation = repo.get_conversation(ws.conn, conversation_id)
                if not conversation:
                    return self._send(404, {"error": "conversation not found"})
                return self._send(200, {"conversation": conversation, "messages": repo.list_messages(ws.conn, conversation_id)})
            finally:
                ws.close()
        if path == "/api/summary":
            ws = _ws()
            try:
                return self._send(200, {
                    "tasks": ws.conn.execute("SELECT COUNT(*) FROM research_tasks").fetchone()[0],
                    "knowledge": ws.conn.execute("SELECT COUNT(*) FROM concepts").fetchone()[0],
                    "projects": ws.conn.execute("SELECT COUNT(*) FROM projects WHERE status='active'").fetchone()[0],
                })
            finally:
                ws.close()
        if path == "/api/tasks":
            demo_only = parse_qs(urlparse(self.path).query).get("demo", ["0"])[0] == "1"
            ws = _ws()
            try:
                rows = ws.conn.execute(
                    "SELECT id,goal,status,created_at,finished_at,plan FROM research_tasks "
                    "ORDER BY created_at DESC LIMIT 100").fetchall()
                items = []
                for row in rows:
                    item = dict(row)
                    try:
                        item["demo"] = bool(json.loads(item.pop("plan") or "{}").get("demo"))
                    except (TypeError, ValueError):
                        item.pop("plan", None)
                        item["demo"] = False
                    if not demo_only or item["demo"]:
                        items.append(item)
                    if len(items) >= 20:
                        break
                return self._send(200, items)
            finally:
                ws.close()
        if path.startswith("/api/task/"):
            task_id = path.split("/")[-1]
            return self._task_detail(task_id)
        if path == "/api/knowledge":
            q = parse_qs(urlparse(self.path).query).get("q", [""])[0]
            ws = _ws()
            try:
                if q:
                    return self._send(200, repo.search_concepts(ws.conn, q, limit=30))
                return self._send(200, repo.list_concepts(ws.conn))
            finally:
                ws.close()
        if path == "/api/experiments":
            ws = _ws()
            try:
                items = repo.list_experiments(ws.conn)
                for item in items:
                    output = Path(item["output_dir"]) if item.get("output_dir") else None
                    item["files"] = sorted(p.name for p in output.iterdir() if p.is_file()) if output and output.exists() else []
                return self._send(200, items)
            finally:
                ws.close()
        if path == "/api/projects":
            ws = _ws()
            try:
                return self._send(200, repo.list_projects(ws.conn))
            finally:
                ws.close()
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/conversation":
            data = self._read_json()
            conversation_id = uuid.uuid4().hex[:12]
            title = (data.get("title") or "新对话").strip()[:80]
            ws = _ws()
            try:
                return self._send(200, repo.create_conversation(ws.conn, conversation_id, title))
            finally:
                ws.close()
        if path.startswith("/api/conversation/") and path.endswith("/message"):
            conversation_id = path.split("/")[3]
            data = self._read_json()
            content = (data.get("content") or "").strip()
            if not content:
                return self._send(400, {"error": "message cannot be empty"})
            ws = _ws()
            try:
                if not repo.get_conversation(ws.conn, conversation_id):
                    return self._send(404, {"error": "conversation not found"})
                repo.add_message(ws.conn, conversation_id, "user", content)
                conversation = repo.get_conversation(ws.conn, conversation_id) or {}
                if conversation.get("title") in {None, "", "新对话"}:
                    repo.update_conversation(ws.conn, conversation_id, title=content[:36])
                goal = _conversation_goal(ws, conversation_id, content)
            finally:
                ws.close()
            task_id = _start_research(goal, conversation_id=conversation_id)
            return self._send(200, {"conversation_id": conversation_id, "task_id": task_id})
        if path.startswith("/api/conversation/") and path.endswith("/remember"):
            conversation_id = path.split("/")[3]
            data = self._read_json()
            task_id = data.get("task_id")
            want_ids = set(int(i) for i in (data.get("update_ids") or []))
            ws = _ws()
            try:
                applied = _apply_task_updates(ws, task_id, want_ids or None)
                if applied:
                    repo.add_message(ws.conn, conversation_id, "system",
                                     "已记住：" + "、".join(applied), kind="memory",
                                     task_id=task_id, metadata={"concepts": applied})
                return self._send(200, {"applied": applied, "count": len(applied)})
            finally:
                ws.close()
        if path == "/api/research":
            data = self._read_json()
            goal = (data.get("goal") or "").strip()
            if not goal:
                return self._send(400, {"error": "goal 不能为空"})
            task_id = _start_research(goal)
            return self._send(200, {"task_id": task_id})
        if path == "/api/task/apply":
            data = self._read_json()
            task_id = data.get("task_id")
            want_ids = set(int(i) for i in (data.get("update_ids") or []))
            ws = _ws()
            try:
                applied = _apply_task_updates(ws, task_id, want_ids or None)
                return self._send(200, {"applied": applied, "count": len(applied)})
            finally:
                ws.close()
        if path == "/api/frontier":
            ws = _ws()
            try:
                try:
                    cfg = load_config()
                    cfg.llm_timeout_s = 40  # 选题是交互动作，LLM 超时压短，避免长时间转圈
                    ctx = repo.list_concepts(ws.conn)
                    recent = [dict(r) for r in ws.conn.execute(
                        "SELECT goal, created_at FROM research_tasks WHERE status='finished' ORDER BY created_at DESC LIMIT 5"
                    ).fetchall()]
                    projects = repo.list_projects(ws.conn, status="active")
                    report = generate_frontier(cfg, LLMClient(cfg), ctx, recent, projects)
                    return self._send(200, report)
                except Exception as e:  # noqa: BLE001
                    # 网关失败/超时 → 降级为示例选题（明确标注），绝不卡死
                    mock_cfg = load_config()
                    mock_cfg.llm_mode = "mock"
                    mock = generate_frontier(mock_cfg, LLMClient(mock_cfg), [], [])
                    mock["error"] = f"实时选题失败，已降级为示例选题：{e}"
                    return self._send(200, mock)
            finally:
                ws.close()
        if path == "/api/knowledge/seed":
            data = self._read_json()
            names = [n.strip() for n in (data.get("names") or "").split(",") if n.strip()]
            ws = _ws()
            try:
                n = repo.seed_concepts(ws.conn, [{"name": nm, "status": "known", "confidence": "high"} for nm in names])
                return self._send(200, {"seeded": n})
            finally:
                ws.close()
        if path == "/api/project/add":
            from lodestar.project import ingest_github
            data = self._read_json()
            try:
                info = ingest_github(data.get("url") or "")
            except Exception as e:  # noqa: BLE001
                return self._send(200, {"error": f"摄入失败：{e}"})
            ws = _ws()
            try:
                pid = repo.upsert_project(ws.conn, info["name"], url=info["url"],
                                          description=info.get("description"),
                                          tech_stack=info.get("tech_stack"),
                                          status=data.get("status") or "active")
                return self._send(200, {"id": pid, "name": info["name"], "tech_stack": info.get("tech_stack")})
            finally:
                ws.close()
        if path == "/api/project/status":
            data = self._read_json()
            ws = _ws()
            try:
                repo.set_project_status(ws.conn, int(data.get("id")), data.get("status") or "idea")
                return self._send(200, {"ok": True})
            finally:
                ws.close()
        if path == "/api/quiz/start":
            data = self._read_json()
            ws = _ws()
            try:
                names = data.get("concepts") or []
                if not names:  # 自动挑：partial/unknown 优先，其次 notes 少的，最多 5 个
                    allc = repo.list_concepts(ws.conn)
                    ranked = sorted(allc, key=lambda c: (c["status"] not in {"partial", "unknown"}, c["status"] == "known", -len(c["notes"])))
                    names = [c["name"] for c in ranked[:5]]
                if not names:
                    return self._send(200, {"error": "知识库为空，先 seed 或研究几次再评估"})
                cfg = load_config(); cfg.llm_timeout_s = 40
                ctx_txt = _concepts_text(repo.list_concepts(ws.conn))
                q = quiz_question(cfg, LLMClient(cfg), names[0], ctx_txt)
                return self._send(200, {"concepts": names, "index": 0, "concept": names[0], "question": q})
            except Exception as e:  # noqa: BLE001
                return self._send(200, {"error": f"出题失败：{e}"})
            finally:
                ws.close()
        if path == "/api/quiz/answer":
            data = self._read_json()
            concept, question, answer = data.get("concept"), data.get("question"), data.get("answer")
            if not answer or not answer.strip():
                return self._send(400, {"error": "回答不能为空"})
            ws = _ws()
            try:
                ctx_txt = _concepts_text(repo.list_concepts(ws.conn))
                cfg = load_config(); cfg.llm_timeout_s = 40
                try:
                    verdict = quiz_evaluate(cfg, LLMClient(cfg), concept, question, answer, ctx_txt)
                except Exception as e:  # noqa: BLE001
                    return self._send(200, {"error": f"评估失败：{e}"})
                repo.upsert_concept(ws.conn, concept, status=verdict["status"], confidence=verdict["confidence"],
                                    append_note=f"[测评] {question[:40]}… → {verdict['status']}/{verdict['confidence']}。{verdict['feedback']}")
                return self._send(200, verdict)
            finally:
                ws.close()
        if path == "/api/quiz/next":
            data = self._read_json()
            names, idx = data.get("concepts") or [], int(data.get("index") or 0)
            if idx >= len(names):
                return self._send(200, {"done": True})
            cfg = load_config(); cfg.llm_timeout_s = 40
            try:
                q = quiz_question(cfg, LLMClient(cfg), names[idx], "")
                return self._send(200, {"concept": names[idx], "question": q, "done": False})
            except Exception as e:  # noqa: BLE001
                return self._send(200, {"error": f"出题失败：{e}"})
        if path == "/api/experiment/build":
            data = self._read_json()
            ws = _ws()
            try:
                exp = repo.get_experiment(ws.conn, int(data.get("exp_id")))
                if not exp:
                    return self._send(404, {"error": "实验不存在"})
                out_root = load_config().workspace_dir / "experiments"
                project = scaffold_experiment(exp, out_root)
                repo.set_experiment_build(ws.conn, exp["id"], "built", str(project))
                return self._send(200, {"project": str(project), "status": "built"})
            finally:
                ws.close()
        if path == "/api/experiment/save":
            data = self._read_json()
            ws = _ws()
            try:
                task = repo.get_task(ws.conn, data.get("task_id"))
                if not task:
                    return self._send(404, {"error": "task 不存在"})
                opts = extract_opportunities(task.get("brief_md") or "")
                if not opts:
                    return self._send(400, {"error": "该任务无 Project Opportunities"})
                idx = (int(data.get("pick") or 1)) - 1
                idx = max(0, min(idx, len(opts) - 1))
                exp_id = repo.add_experiment(ws.conn, opts[idx], task_id=task["id"],
                                            description="从 Research Brief 提取的可验证假设，下一步比较 baseline / candidate 并运行 eval.py。")
                return self._send(200, {"exp_id": exp_id, "hypothesis": opts[idx][:80]})
            finally:
                ws.close()
        return self._send(404, {"error": "not found"})

    def _task_detail(self, task_id: str):
        ws = _ws()
        try:
            task = repo.get_task(ws.conn, task_id)
            if not task:
                return self._send(404, {"error": f"task {task_id} 不存在"})
            return self._send(200, {
                "task": {k: task[k] for k in ("id", "goal", "status", "metrics", "created_at", "finished_at")},
                "brief_md": task.get("brief_md") or "",
                "sources": repo.list_sources(ws.conn, task_id),
                "updates": repo.list_knowledge_updates(ws.conn, task_id=task_id),
                "trace": repo.list_trace_events(ws.conn, task_id),
                "opportunities": extract_opportunities(task.get("brief_md") or ""),
            })
        finally:
            ws.close()


def serve(port: int = 8123, open_browser: bool = True) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Lodestar UI: http://127.0.0.1:{port}")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(f"http://127.0.0.1:{port}")).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


# ----------------------------------------------------------------------
# 页面
# ----------------------------------------------------------------------
PAGE_HTML = '<!doctype html>\n<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Lodestar · 研究对话</title>\n<style>\n:root{--bg:#0d1117;--panel:#141b24;--panel2:#1b2530;--line:#2b3744;--fg:#f5efe5;--mut:#9ea6ad;--acc:#ed9342;--acc2:#bd6522;--ok:#82c78c;--warn:#e3a06f;--shadow:0 24px 60px rgba(0,0,0,.22)}\n[data-theme=light]{--bg:#f4efe6;--panel:#fffdf8;--panel2:#f1e6d7;--line:#e2d5c5;--fg:#241f1a;--mut:#71675d;--acc:#bd6d25;--acc2:#9d5418;--ok:#38704a;--warn:#a85527;--shadow:0 20px 48px rgba(103,76,42,.12)}\n*{box-sizing:border-box}html{background:var(--bg);scroll-behavior:smooth}body{margin:0;min-height:100vh;background:radial-gradient(circle at 78% -10%,rgba(237,147,66,.12),transparent 34%),var(--bg);color:var(--fg);font:14px/1.65 "Segoe UI","Microsoft YaHei","Noto Sans SC",Arial,sans-serif}body:before{content:"";position:fixed;inset:0;pointer-events:none;opacity:.13;background-image:radial-gradient(circle,var(--line) 1px,transparent 1px);background-size:36px 36px}button,input,textarea{font:inherit}button{cursor:pointer}button:focus-visible,input:focus-visible,textarea:focus-visible{outline:2px solid var(--acc);outline-offset:3px}.mut,.small{color:var(--mut)}.small{font-size:12px}.eyebrow{font-size:10px;letter-spacing:.16em;text-transform:uppercase;color:var(--acc);font-weight:800}\n.app-shell{position:relative;z-index:1;display:grid;grid-template-columns:250px minmax(0,1fr);min-height:100vh}.rail{display:flex;flex-direction:column;padding:28px 18px;border-right:1px solid var(--line);background:color-mix(in srgb,var(--panel) 88%,transparent)}.brand{padding:0 12px 28px}.brand h1{margin:0;font:700 25px/1.1 Georgia,"Microsoft YaHei",serif;letter-spacing:-.04em}.brand h1:before{content:"✦";color:var(--acc);font:18px/1 Arial;margin-right:8px;vertical-align:3px}.brand p{margin:9px 0 0 28px;color:var(--mut);font-size:11px}.rail-label{padding:0 12px;margin:8px 0;font-size:10px;color:var(--mut);letter-spacing:.16em;text-transform:uppercase}.nav{display:grid;gap:4px}.nav button{display:flex;align-items:center;gap:10px;width:100%;padding:10px 12px;border:0;border-radius:9px;background:transparent;color:var(--mut);text-align:left;font-weight:650}.nav button:hover{color:var(--fg);background:var(--panel2)}.nav button.on{color:var(--fg);background:var(--panel2);box-shadow:inset 3px 0 var(--acc)}.nav-mark{display:grid;place-items:center;width:22px;color:var(--acc);font-size:12px}.theme-toggle{margin:18px 12px 0;padding:8px 10px;border:1px solid var(--line);border-radius:8px;background:transparent;color:var(--mut);text-align:left}.theme-toggle:hover{border-color:var(--acc);color:var(--fg)}.rail-note{margin-top:auto;padding:15px 12px;border:1px solid var(--line);border-radius:11px;background:linear-gradient(145deg,var(--panel2),var(--panel));color:var(--mut);font-size:12px;line-height:1.6}.rail-note strong{display:block;color:var(--fg);font-size:12px;margin-bottom:3px}.signal{display:inline-flex;align-items:center;gap:5px;color:var(--ok);font-size:11px}.signal:before{content:"";width:6px;height:6px;border-radius:50%;background:var(--ok)}\n.workspace{min-width:0;max-width:1180px;width:100%;padding:30px 42px 60px}.topbar{display:flex;justify-content:space-between;align-items:flex-start;gap:18px;margin-bottom:24px}.topbar h2,.subhead h2{margin:0;font:700 clamp(24px,3vw,36px)/1.12 Georgia,"Microsoft YaHei",serif;letter-spacing:-.045em}.topbar p,.subhead p{margin:8px 0 0;color:var(--mut);font-size:13px}.status-pill{display:inline-flex;align-items:center;gap:7px;padding:7px 10px;border:1px solid var(--line);border-radius:999px;color:var(--mut);font-size:11px;white-space:nowrap}.status-pill:before{content:"";width:7px;height:7px;border-radius:50%;background:var(--ok)}.view{display:none}.view.on{display:block;animation:rise .35s ease both}@keyframes rise{from{opacity:0;transform:translateY(7px)}to{opacity:1;transform:none}}\n.chat-frame{display:grid;grid-template-columns:minmax(0,1fr) 250px;gap:18px;align-items:start}.chat-panel{min-width:0;border:1px solid var(--line);border-radius:16px;background:var(--panel);box-shadow:var(--shadow);overflow:hidden}.chat-header{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:16px 20px;border-bottom:1px solid var(--line);background:linear-gradient(90deg,color-mix(in srgb,var(--acc) 9%,transparent),transparent 55%)}.chat-header strong{font-size:13px}.chat-header span{font-size:11px;color:var(--mut)}.chat-actions{display:flex;gap:7px}.icon-btn,.ghost,.primary{padding:8px 12px;border-radius:8px;font-weight:700;font-size:12px}.icon-btn,.ghost{border:1px solid var(--line);background:transparent;color:var(--mut)}.icon-btn:hover,.ghost:hover{border-color:var(--acc);color:var(--acc)}.primary{border:0;background:var(--acc);color:#1c1007;box-shadow:0 9px 20px color-mix(in srgb,var(--acc) 18%,transparent)}.primary:hover{background:var(--acc2);color:#fff8ef}.chat-scroll{min-height:470px;max-height:calc(100vh - 280px);overflow:auto;padding:24px 22px 12px}.welcome{max-width:570px;padding:26px 24px;margin:10px auto 24px;border-left:3px solid var(--acc);background:linear-gradient(135deg,color-mix(in srgb,var(--acc) 10%,var(--panel)),transparent 75%);border-radius:0 12px 12px 0}.welcome h3{margin:10px 0 7px;font:700 25px/1.2 Georgia,"Microsoft YaHei",serif;letter-spacing:-.03em}.welcome p{margin:0;color:var(--mut);line-height:1.7}.suggestions{display:flex;flex-wrap:wrap;gap:7px;margin-top:17px}.suggestions button{padding:7px 10px;border:1px solid var(--line);border-radius:999px;background:var(--panel);color:var(--mut);font-size:11px}.suggestions button:hover{border-color:var(--acc);color:var(--acc)}.message{display:flex;margin:18px 0}.message.user{justify-content:flex-end}.bubble{max-width:88%;padding:12px 15px;border-radius:13px;color:var(--fg);line-height:1.7}.message.user .bubble{background:var(--acc);color:#201107;border-bottom-right-radius:4px;font-weight:600}.message.system .bubble{background:color-mix(in srgb,var(--ok) 12%,var(--panel));border:1px solid color-mix(in srgb,var(--ok) 45%,var(--line));color:var(--ok)}.research-card{width:min(100%,720px);border:1px solid var(--line);border-left:3px solid var(--acc);border-radius:0 13px 13px 0;background:linear-gradient(145deg,var(--panel2),var(--panel));padding:17px 18px}.research-meta{display:flex;justify-content:space-between;gap:12px;color:var(--mut);font-size:10px;margin-bottom:9px}.brief h2{font:700 17px/1.35 Georgia,"Microsoft YaHei",serif;color:var(--acc);margin:20px 0 9px}.brief h3{font-size:14px;margin:15px 0 7px}.brief p{margin:7px 0;line-height:1.75}.brief ul{padding-left:20px}.brief li{margin:4px 0}.brief a{color:var(--acc)}.brief strong{font-weight:800;color:var(--fg)}.sources{margin-top:14px;padding-top:13px;border-top:1px solid var(--line)}.sources-title{font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:var(--mut);font-weight:800}.source-row{display:flex;gap:9px;align-items:flex-start;padding:8px 0;border-bottom:1px solid color-mix(in srgb,var(--line) 70%,transparent)}.source-row:last-child{border-bottom:0}.source-num{display:grid;place-items:center;width:20px;height:20px;border-radius:50%;background:var(--acc);color:#1e1007;font-size:10px;font-weight:800;flex:0 0 auto}.source-row a{color:var(--fg);font-size:12px;line-height:1.45;text-decoration:none}.source-row a:hover{color:var(--acc)}.source-row small{display:block;color:var(--mut);font-size:10px;margin-top:2px}.memory-card{margin-top:14px;padding:13px 14px;border:1px solid color-mix(in srgb,var(--acc) 50%,var(--line));border-radius:10px;background:color-mix(in srgb,var(--acc) 9%,var(--panel))}.memory-card strong{display:block;color:var(--acc);font-size:12px}.memory-card p{margin:4px 0 10px;color:var(--mut);font-size:12px}.memory-card label{display:block;padding:8px 9px;margin:6px 0;border:1px solid var(--line);border-radius:8px;background:var(--panel);font-size:12px}.memory-actions{display:flex;gap:7px;margin-top:9px}.typing{display:inline-flex;gap:4px;align-items:center;color:var(--mut);font-size:12px;padding:11px 14px;background:var(--panel2);border-radius:12px}.typing i{width:5px;height:5px;border-radius:50%;background:var(--acc);animation:blink 1.1s infinite}@keyframes blink{0%,60%,100%{opacity:.25}30%{opacity:1}}.composer{padding:14px 16px 16px;border-top:1px solid var(--line);background:color-mix(in srgb,var(--panel2) 55%,var(--panel))}.composer-box{display:flex;align-items:flex-end;gap:9px;padding:8px 9px 8px 13px;border:1px solid var(--line);border-radius:12px;background:var(--panel)}.composer-box:focus-within{border-color:var(--acc);box-shadow:0 0 0 3px color-mix(in srgb,var(--acc) 12%,transparent)}.composer textarea{flex:1;min-height:42px;max-height:130px;padding:6px 0;border:0;resize:none;background:transparent;color:var(--fg);outline:0;line-height:1.55}.composer textarea::placeholder{color:var(--mut)}.composer-foot{display:flex;justify-content:space-between;margin-top:7px;padding:0 2px;color:var(--mut);font-size:10px}.context-panel{display:grid;gap:12px}.context-card,.list-card,.exp-card{padding:16px;border:1px solid var(--line);border-radius:13px;background:var(--panel);box-shadow:0 12px 28px rgba(0,0,0,.07)}.context-card h3{margin:0 0 9px;font-size:12px}.context-card p{margin:0;color:var(--mut);font-size:12px;line-height:1.65}.context-list{display:grid;gap:7px;margin-top:10px}.context-list div{display:flex;justify-content:space-between;gap:12px;font-size:11px;color:var(--mut)}.context-list strong{color:var(--fg);font-size:12px}.subhead{margin:6px 0 18px}.toolbar{display:flex;gap:8px;flex-wrap:wrap;margin:15px 0}.field{border:1px solid var(--line);border-radius:9px;background:var(--panel);color:var(--fg);padding:10px 12px;min-width:220px}.list{display:grid;gap:10px}.list-card{border-left:3px solid var(--line)}.list-card:hover{border-left-color:var(--acc)}.list-card strong{font-size:13px}.list-meta{display:flex;gap:8px;flex-wrap:wrap;margin-top:7px;color:var(--mut);font-size:11px}.tag{padding:2px 7px;border:1px solid var(--line);border-radius:999px;color:var(--mut);font-size:10px}.empty{padding:42px 18px;text-align:center;color:var(--mut);border:1px dashed var(--line);border-radius:12px}.empty b{display:block;color:var(--acc);font:25px Georgia;margin-bottom:6px}.exp-card h3{margin:8px 0 5px;font:700 17px Georgia,"Microsoft YaHei",serif}.exp-card p{margin:0;color:var(--mut);font-size:12px}.exp-actions{display:flex;gap:8px;margin-top:13px;flex-wrap:wrap}\n@media(max-width:900px){.app-shell{grid-template-columns:76px minmax(0,1fr)}.rail{padding:22px 10px}.brand{padding:0 0 24px;text-align:center}.brand h1{font-size:0}.brand h1:before{font-size:21px;margin:0}.brand p,.rail-label,.rail-note{display:none}.nav button{justify-content:center;padding:11px 8px}.nav button span:last-child{display:none}.theme-toggle{margin:18px 0 0;font-size:0;text-align:center}.theme-toggle:before{content:"☼";font-size:15px}.workspace{padding:25px 22px 45px}.chat-frame{grid-template-columns:1fr}.context-panel{grid-template-columns:repeat(2,minmax(0,1fr))}.chat-scroll{max-height:none}}\n@media(max-width:600px){.app-shell{display:block}.rail{position:sticky;top:0;z-index:5;display:flex;flex-direction:row;align-items:center;padding:10px 12px;border-right:0;border-bottom:1px solid var(--line)}.brand{padding:0;margin-right:12px}.nav{display:flex;overflow:auto}.nav button{min-width:44px}.theme-toggle{margin:0 0 0 auto}.workspace{padding:22px 14px 35px}.topbar{display:block}.status-pill{margin-top:13px}.context-panel{grid-template-columns:1fr}.chat-scroll{padding:18px 13px 10px}.welcome{padding:21px 18px}.bubble{max-width:95%}.research-card{width:100%}.composer{padding:10px}.composer-foot{display:none}}\n@media(prefers-reduced-motion:reduce){*,*:before{animation:none!important;transition:none!important;scroll-behavior:auto!important}}\n</style></head><body>\n<div class="app-shell"><aside class="rail"><div class="brand"><h1>Lodestar</h1><p>把问题带到证据，把理解留在记忆里。</p></div><div class="rail-label">Workspace</div><nav class="nav" id="nav"><button class="on" data-view="chat"><span class="nav-mark">◌</span><span>研究对话</span></button><button data-view="knowledge"><span class="nav-mark">⌁</span><span>知识库</span></button><button data-view="history"><span class="nav-mark">↺</span><span>研究历史</span></button><button data-view="experiment"><span class="nav-mark">◇</span><span>实验项目</span></button><button data-view="project"><span class="nav-mark">▦</span><span>我的项目</span></button></nav><button class="theme-toggle" id="themeBtn" onclick="toggleTheme()">☼ 浅色主题</button><div class="rail-note"><strong>当前模式</strong><span class="signal">Research harness online</span><br><span>Agent 会根据问题决定搜索、解释或继续追问。</span></div></aside>\n<main class="workspace"><div class="topbar"><div><div class="eyebrow">AI RESEARCH COMPANION</div><h2 id="pageTitle">研究对话</h2><p id="pageCopy">不用切换页面。把你想知道的事直接告诉 Lodestar。</p></div><div class="status-pill" id="statusPill">准备好了</div></div>\n<section class="view on" id="view-chat"><div class="chat-frame"><div class="chat-panel"><div class="chat-header"><div><strong id="conversationTitle">新对话</strong><span id="conversationMeta"> · 你的研究上下文</span></div><div class="chat-actions"><button class="icon-btn" onclick="newConversation()">＋ 新对话</button></div></div><div class="chat-scroll" id="chatScroll"><div class="welcome" id="welcome"><div class="eyebrow">START FROM A QUESTION</div><h3>今天想弄懂什么？</h3><p>我可以搜索本周学术热点，读论文和 PDF，讲清楚它们与你当前项目的关系，再把真正有用的结论留进你的知识库。</p><div class="suggestions"><button onclick="useSuggestion(this)">找本周 Agent 热点，先讲最重要的一条</button><button onclick="useSuggestion(this)">解释 Agent Memory 和 Reflection Loop</button><button onclick="useSuggestion(this)">看看这些研究对 Lodestar 有什么启发</button></div></div><div id="chatMessages"></div></div><form class="composer" id="composer"><div class="composer-box"><textarea id="chatInput" rows="1" placeholder="例如：帮我找本周 Agent Memory 的热点，讲给我听"></textarea><button class="primary" id="sendBtn" type="submit">发送 ↗</button></div><div class="composer-foot"><span>Enter 发送 · Shift + Enter 换行</span><span>Sources · Trace · Memory</span></div></form></div><aside class="context-panel"><div class="context-card"><div class="eyebrow">IN THIS SPACE</div><h3>对话会记住什么</h3><p>当前主题、已读来源、你的追问，以及你明确说“记住”的知识更新。</p><div class="context-list"><div><span>Knowledge State</span><strong id="ctxKnowledge">—</strong></div><div><span>Research runs</span><strong id="ctxTasks">—</strong></div><div><span>Active projects</span><strong id="ctxProjects">—</strong></div></div></div><div class="context-card"><div class="eyebrow">NEXT MOVE</div><h3>把它当成一位研究搭档</h3><p>先问一个具体问题。你可以随时说“展开第二点”“和我之前知道的有什么不同”或“记住这条”。</p></div></aside></div></section>\n<section class="view" id="view-knowledge"><div class="subhead"><div><div class="eyebrow">KNOWLEDGE STATE</div><h2>你的认知地图</h2><p>这里是可浏览的记忆底座，真正的更新发生在研究对话里。</p></div></div><div class="toolbar"><input class="field" id="kq" placeholder="搜索概念…"><button class="ghost" onclick="loadKnowledge()">搜索</button></div><div class="list" id="kArea"></div></section>\n<section class="view" id="view-history"><div class="subhead"><div><div class="eyebrow">RESEARCH LOG</div><h2>研究历史</h2><p>每一次对话都能回到它的来源、证据和下一步。</p></div></div><div class="list" id="hArea"></div></section>\n<section class="view" id="view-experiment"><div class="subhead"><div><div class="eyebrow">EXPERIMENT LAB</div><h2>从洞察到实验</h2><p>只有当你准备验证一个判断时，才把它带到这里。</p></div></div><div class="list" id="eArea"></div></section>\n<section class="view" id="view-project"><div class="subhead"><div><div class="eyebrow">PROJECTS</div><h2>我的项目</h2><p>让研究回答“这对我正在做的事有什么用”。</p></div></div><div class="toolbar"><input class="field" id="purl" placeholder="GitHub 仓库链接"><button class="ghost" onclick="addProject()">登记项目</button></div><div class="list" id="pArea"></div></section></main></div>\n<script>\nconst $=s=>document.querySelector(s);let conversationId=null,pollTimer=null;\nfunction esc(s){return String(s||\'\').replace(/&/g,\'&amp;\').replace(/</g,\'&lt;\').replace(/>/g,\'&gt;\').replace(/"/g,\'&quot;\');}\nfunction md(t){if(!t)return\'\';t=esc(t);t=t.replace(/^### (.*)$/gm,\'<h3>$1</h3>\').replace(/^## (.*)$/gm,\'<h2>$1</h2>\').replace(/^# (.*)$/gm,\'<h2>$1</h2>\').replace(/\\*\\*(.*?)\\*\\*/g,\'<strong>$1</strong>\').replace(/\\[([^\\]]+)\\]\\((https?:[^)]+)\\)/g,\'<a href="$2" target="_blank" rel="noreferrer">$1</a>\').replace(/^- (.*)$/gm,\'<li>$1</li>\');return t.split(/\\n\\n+/).map(x=>x.match(/^<(h2|h3|li)/)?x:\'<p>\'+x.replace(/\\n/g,\'<br>\')+\'</p>\').join(\'\');}\nfunction formatDate(v){if(!v)return\'时间未知\';const d=new Date(v);if(Number.isNaN(d.getTime()))return v;return new Intl.DateTimeFormat(\'zh-CN\',{timeZone:\'Asia/Shanghai\',month:\'2-digit\',day:\'2-digit\',hour:\'2-digit\',minute:\'2-digit\',hour12:false}).format(d);}\nfunction setStatus(t){$(\'#statusPill\').textContent=t;}function updateTheme(){const light=document.documentElement.dataset.theme===\'light\';$(\'#themeBtn\').textContent=light?\'☾ 深色主题\':\'☼ 浅色主题\';}function toggleTheme(){const h=document.documentElement;h.dataset.theme=h.dataset.theme===\'light\'?\'\':\'light\';localStorage.setItem(\'lodestar-theme\',h.dataset.theme);updateTheme();}if(localStorage.getItem(\'lodestar-theme\')===\'light\')document.documentElement.dataset.theme=\'light\';updateTheme();\nasync function jget(p){const r=await fetch(p);if(!r.ok)throw Error(\'HTTP \'+r.status);return r.json();}async function jpost(p,b){const r=await fetch(p,{method:\'POST\',headers:{\'Content-Type\':\'application/json\'},body:JSON.stringify(b||{})});if(!r.ok)throw Error(\'HTTP \'+r.status);return r.json();}\nfunction switchView(name){document.querySelectorAll(\'.nav button\').forEach(b=>b.classList.toggle(\'on\',b.dataset.view===name));document.querySelectorAll(\'.view\').forEach(v=>v.classList.toggle(\'on\',v.id===\'view-\'+name));const copy={chat:[\'研究对话\',\'不用切换页面。把你想知道的事直接告诉 Lodestar。\'],knowledge:[\'你的认知地图\',\'这里是可浏览的记忆底座，真正的更新发生在研究对话里。\'],history:[\'研究历史\',\'每一次对话都能回到它的来源、证据和下一步。\'],experiment:[\'从洞察到实验\',\'只有当你准备验证一个判断时，才把它带到这里。\'],project:[\'我的项目\',\'让研究回答“这对我正在做的事有什么用”。\']}[name];$(\'#pageTitle\').textContent=copy[0];$(\'#pageCopy\').textContent=copy[1];if(name===\'knowledge\')loadKnowledge();if(name===\'history\')loadHistory();if(name===\'experiment\')loadExperiments();if(name===\'project\')loadProjects();}$(\'#nav\').onclick=e=>{const b=e.target.closest(\'button[data-view]\');if(b)switchView(b.dataset.view);};function useSuggestion(b){$(\'#chatInput\').value=b.textContent;$(\'#chatInput\').focus();}\nasync function newConversation(){const c=await jpost(\'/api/conversation\',{title:\'新对话\'});conversationId=c.id;$(\'#conversationTitle\').textContent=c.title;$(\'#chatMessages\').innerHTML=\'\';$(\'#welcome\').style.display=\'block\';setStatus(\'准备好了\');$(\'#chatInput\').focus();}\nasync function ensureConversation(){if(conversationId)return conversationId;const list=await jget(\'/api/conversations\');if(list.length){conversationId=list[0].id;return conversationId;}const c=await jpost(\'/api/conversation\',{title:\'新对话\'});conversationId=c.id;return conversationId;}\nfunction addUserBubble(content){$(\'#welcome\').style.display=\'none\';$(\'#chatMessages\').insertAdjacentHTML(\'beforeend\',\'<div class="message user"><div class="bubble">\'+esc(content).replace(/\\n/g,\'<br>\')+\'</div></div>\');scrollChat();}function addTyping(){const id=\'typing-\'+Date.now();$(\'#chatMessages\').insertAdjacentHTML(\'beforeend\',\'<div class="message" id="\'+id+\'"><div class="typing"><i></i><i></i><i></i><span>正在理解问题并选择研究路径…</span></div></div>\');scrollChat();return id;}function scrollChat(){const el=$(\'#chatScroll\');requestAnimationFrame(()=>el.scrollTop=el.scrollHeight);}\nasync function sendMessage(e){e.preventDefault();const input=$(\'#chatInput\'),content=input.value.trim();if(!content)return;input.value=\'\';addUserBubble(content);const typing=addTyping();$(\'#sendBtn\').disabled=true;setStatus(\'研究中\');try{const cid=await ensureConversation();const r=await jpost(\'/api/conversation/\'+cid+\'/message\',{content});conversationId=cid;await pollTask(r.task_id,typing);}catch(err){const node=$(\'#\'+typing);if(node)node.innerHTML=\'<div class="bubble" style="color:var(--warn)">暂时无法启动研究：\'+esc(err.message)+\'</div>\';setStatus(\'需要重试\');}finally{$(\'#sendBtn\').disabled=false;}}$(\'#composer\').onsubmit=sendMessage;$(\'#chatInput\').addEventListener(\'keydown\',e=>{if(e.key===\'Enter\'&&!e.shiftKey){e.preventDefault();$(\'#composer\').requestSubmit();}});\nasync function pollTask(id,typing){let tick=0;clearInterval(pollTimer);pollTimer=setInterval(async()=>{const d=await jget(\'/api/task/\'+id);const node=$(\'#\'+typing);if(node){const label=node.querySelector(\'span\');if(label)label.textContent=\'研究中… \'+(++tick)+\'s · 已记录 \'+((d.trace||[]).length)+\' 个步骤\';}if(d.task&&(d.task.status===\'finished\'||d.task.status===\'error\')){clearInterval(pollTimer);await loadConversation();setStatus(d.task.status===\'finished\'?\'研究完成\':\'研究失败\');}},2000);}\nasync function loadConversation(){if(!conversationId)return;const d=await jget(\'/api/conversation/\'+conversationId);$(\'#conversationTitle\').textContent=d.conversation.title;const msgs=d.messages||[];$(\'#chatMessages\').innerHTML=\'\';$(\'#welcome\').style.display=msgs.length?\'none\':\'block\';for(const m of msgs){if(m.role===\'user\')$(\'#chatMessages\').insertAdjacentHTML(\'beforeend\',\'<div class="message user"><div class="bubble">\'+esc(m.content).replace(/\\n/g,\'<br>\')+\'</div></div>\');else if(m.kind===\'memory\')$(\'#chatMessages\').insertAdjacentHTML(\'beforeend\',\'<div class="message system"><div class="bubble">✦ \'+esc(m.content)+\'</div></div>\');else if(m.kind===\'research\'){const wrap=document.createElement(\'div\');wrap.className=\'message\';wrap.innerHTML=\'<article class="research-card"><div class="research-meta"><span>RESEARCH BRIEF</span><span>\'+formatDate(m.created_at)+\'</span></div><div class="brief">\'+md(m.content)+\'</div><div class="research-extra" data-task="\'+esc(m.task_id||\'\')+\'"></div></article>\';$(\'#chatMessages\').appendChild(wrap);if(m.task_id)decorateResearch(wrap.querySelector(\'.research-extra\'),m.task_id);}}scrollChat();}\nasync function decorateResearch(el,taskId){try{const d=await jget(\'/api/task/\'+taskId);const sources=(d.sources||[]).slice(0,4);let h=sources.length?\'<div class="sources"><div class="sources-title">Evidence trail · \'+sources.length+\' sources</div>\'+sources.map((s,i)=>\'<div class="source-row"><span class="source-num">\'+(i+1)+\'</span><div><a href="\'+esc(s.url)+\'" target="_blank" rel="noreferrer">\'+esc(s.title)+\'</a><small>\'+esc(s.venue||s.source_type||\'source\')+(s.read_depth===\'full\'?\' · PDF full text\':\'\')+\'</small></div></div>\').join(\'\')+\'</div>\':\'\';const pending=(d.updates||[]).filter(u=>u.status===\'pending\');if(pending.length){h+=\'<div class="memory-card"><strong>把这次理解留在 Knowledge State</strong><p>选择你想长期记住的结论；它不会自动覆盖已有知识。</p>\'+pending.map(u=>\'<label><input type="checkbox" class="memory-box" value="\'+u.id+\'" checked> <b>\'+esc(u.concept)+\'</b> · \'+esc((u.proposal||{}).claim||\'新的研究判断\')+\'</label>\').join(\'\')+\'<div class="memory-actions"><button class="primary" onclick="rememberUpdates(\\\'\'+esc(taskId)+\'\\\',this)">记住选中的结论</button><button class="ghost" onclick="this.closest(\\\'.memory-card\\\').remove()">这次先不记</button></div></div>\';}el.innerHTML=h;}catch(e){}}\nasync function rememberUpdates(taskId,btn){const card=btn.closest(\'.memory-card\'),ids=[...card.querySelectorAll(\'.memory-box:checked\')].map(x=>+x.value);btn.disabled=true;try{await jpost(\'/api/conversation/\'+conversationId+\'/remember\',{task_id:taskId,update_ids:ids});await loadConversation();setStatus(\'已更新知识\');}catch(e){btn.disabled=false;}}\nasync function loadKnowledge(){const q=$(\'#kq\').value.trim(),d=await jget(\'/api/knowledge\'+(q?\'?q=\'+encodeURIComponent(q):\'\'));$(\'#kArea\').innerHTML=d.map(c=>\'<article class="list-card"><strong>\'+esc(c.name)+\'</strong> <span class="tag">\'+esc(c.status)+\' / \'+esc(c.confidence)+\'</span><div class="list-meta">\'+(c.notes||[]).slice(-3).map(esc).join(\' · \')+\'</div></article>\').join(\'\')||\'<div class="empty"><b>⌁</b>还没有匹配的概念。</div>\';}$(\'#kq\').onkeydown=e=>{if(e.key===\'Enter\')loadKnowledge();};\nasync function loadHistory(){const d=await jget(\'/api/tasks\');$(\'#hArea\').innerHTML=d.map(t=>\'<article class="list-card" onclick="openHistory(\\\'\'+esc(t.id)+\'\\\')"><strong>\'+esc(t.goal||\'未命名研究\')+\'</strong><div class="list-meta"><span class="tag">\'+esc(t.status)+\'</span><span>\'+formatDate(t.created_at)+\'</span></div></article>\').join(\'\')||\'<div class="empty"><b>↺</b>还没有研究记录。</div>\';}async function openHistory(id){const d=await jget(\'/api/task/\'+id);switchView(\'chat\');$(\'#welcome\').style.display=\'none\';$(\'#chatMessages\').innerHTML=\'<div class="message"><article class="research-card"><div class="research-meta"><span>ARCHIVED RESEARCH</span><span>\'+formatDate(d.task.created_at)+\'</span></div><div class="brief">\'+md(d.brief_md)+\'</div></article></div>\';}\nasync function loadExperiments(){const d=await jget(\'/api/experiments\');$(\'#eArea\').innerHTML=d.map(e=>\'<article class="exp-card"><span class="tag">EXPERIMENT #\'+e.id+\' · \'+esc(e.build_status)+\'</span><h3>\'+esc(e.hypothesis||\'未命名实验\')+\'</h3><p>\'+esc(e.description||\'从研究 Brief 提取的可验证假设。\')+\'</p><div class="exp-actions">\'+(e.task_id?\'<button class="ghost" onclick="openHistory(\\\'\'+esc(e.task_id)+\'\\\')">查看研究</button>\':\'\')+\'<button class="primary" onclick="buildExperiment(\'+e.id+\')">\'+(e.build_status===\'built\'?\'重新生成骨架\':\'生成骨架\')+\'</button></div></article>\').join(\'\')||\'<div class="empty"><b>◇</b>从对话中的研究结论保存一个实验假设。</div>\';}async function buildExperiment(id){await jpost(\'/api/experiment/build\',{exp_id:id});await loadExperiments();}async function loadProjects(){const d=await jget(\'/api/projects\');$(\'#pArea\').innerHTML=d.map(p=>\'<article class="list-card"><strong>\'+esc(p.name)+\'</strong> <span class="tag">\'+esc(p.status)+\'</span><div class="list-meta">\'+esc(p.description||\'\')+\' · \'+esc((p.tech_stack||[]).join(\', \'))+\'</div></article>\').join(\'\')||\'<div class="empty"><b>▦</b>登记一个 GitHub 项目，让研究和实际工作连接起来。</div>\';}async function addProject(){const url=$(\'#purl\').value.trim();if(!url)return;const r=await jpost(\'/api/project/add\',{url:url,status:\'active\'});if(!r.error){$(\'#purl\').value=\'\';loadProjects();}}async function loadSummary(){try{const s=await jget(\'/api/summary\');$(\'#ctxKnowledge\').textContent=s.knowledge;$(\'#ctxTasks\').textContent=s.tasks;$(\'#ctxProjects\').textContent=s.projects;}catch(e){}}\n(async function init(){try{const list=await jget(\'/api/conversations\');if(list.length)conversationId=list[0].id;else await newConversation();if(conversationId)await loadConversation();await loadSummary();}catch(e){setStatus(\'本地服务未连接\');}})();\n</script></body></html>'
