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
def _run_research(task_id: str, goal: str, cfg) -> None:
    ws = Workspace(cfg)
    try:
        try:
            result = ResearchAgent(ws, interactive=False).run(goal, apply_updates="pending", task_id=task_id)
        except Exception as e:  # noqa: BLE001
            result = {"error": str(e), "status": "error"}
        if result.get("error") or result.get("status") == "error":
            # 实时研究失败 → 降级为示例研究（mock），UI 永远有结果
            _degrade_to_mock(task_id, goal, result.get("error") or "未知错误")
    finally:
        ws.close()
        _runners.pop(task_id, None)


def _degrade_to_mock(task_id: str, goal: str, error: str) -> None:
    try:
        mock_cfg = load_config()          # 同一主库，仅 LLM/检索切 mock
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
    except Exception:  # noqa: BLE001 —— 降级也失败就保持 error 状态
        pass


def _start_research(goal: str) -> str:
    cfg = load_config()
    ws = Workspace(cfg)
    try:
        task_id = uuid.uuid4().hex[:12]
        repo.create_task(ws.conn, task_id, goal, {}, llm_mode=cfg.llm_mode)  # 预建 running 行
        t = threading.Thread(target=_run_research, args=(task_id, goal, cfg), daemon=True)
        _runners[task_id] = t
        t.start()
        return task_id
    finally:
        ws.close()


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
            ws = _ws()
            try:
                rows = ws.conn.execute(
                    "SELECT id,goal,status,created_at,finished_at FROM research_tasks "
                    "ORDER BY created_at DESC LIMIT 20").fetchall()
                return self._send(200, [dict(r) for r in rows])
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
                pend = repo.list_knowledge_updates(ws.conn, task_id=task_id, status="pending")
                applied = []
                for u in pend:
                    if want_ids and u["id"] not in want_ids:
                        continue  # 只应用勾选的更新
                    p = u["proposal"]
                    repo.upsert_concept(ws.conn, u["concept"], status=p["new_status"],
                                        confidence=p["new_confidence"],
                                        append_note=f"[研究笔记] {p.get('claim')}（novelty={p.get('novelty')}）")
                    repo.set_update_status(ws.conn, u["id"], "applied")
                    applied.append(u["concept"])
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
PAGE_HTML = r"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Lodestar（导星）</title>
<style>
:root{--bg:#0b0f14;--card:#131a22;--line:#2a3542;--fg:#f4efe6;--mut:#a7a29a;--acc:#f09a45;--acc-dim:#c8752a;--ok:#83c28b;--warn:#e8a07b}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.65 "Segoe UI","Microsoft YaHei","Noto Sans SC",Arial,sans-serif;letter-spacing:-.002em}body::before{content:'';position:fixed;inset:0;pointer-events:none;z-index:0;background-image:radial-gradient(circle,var(--line) 1px,transparent 1px);background-size:44px 44px;opacity:.18}[data-theme=light] body::before{opacity:.06}body::after{content:'';position:fixed;inset:0;pointer-events:none;z-index:-1;background:var(--bg)}
header{display:flex;align-items:baseline;gap:12px;padding:20px 28px 0}
header h1{font-size:15px;margin:0;font-weight:600;color:var(--fg);letter-spacing:.01em}header h1::before{content:'✦';color:var(--acc);font-size:16px;margin-right:7px;vertical-align:-1px}
header .v{color:var(--mut);font-size:11px;font-weight:400}
.tabs{display:flex;gap:0;padding:22px 28px 0;border-bottom:1px solid var(--line)}
.tabs button{position:relative;background:none;border:none;color:var(--mut);font-size:14px;padding:10px 0;margin-right:28px;font-weight:600;cursor:pointer;letter-spacing:.02em;transition:color .2s,transform .15s}
.tabs button::after{content:'';position:absolute;left:0;right:0;bottom:-1px;height:2px;background:linear-gradient(90deg,transparent,var(--acc),transparent);opacity:0;transition:opacity .25s;border-radius:2px}
.tabs button:hover{color:var(--fg);transform:translateY(-1px)}
.tabs button.on{color:var(--acc);font-weight:600;transform:none;text-shadow:0 0 12px rgba(232,148,58,.15)}
.tabs button.on::after{opacity:1}
main{padding:28px;max-width:960px;margin:0 auto}
.tab{display:none}.tab.on{display:block}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:20px 24px;margin-bottom:18px;transition:border-color .25s}.card:hover{border-color:var(--acc-dim)}
textarea{width:100%;min-height:72px;background:var(--bg);border:1px solid var(--line);color:var(--fg);border-radius:6px;padding:12px 14px;font:inherit;resize:vertical;transition:border-color .15s}
textarea:focus,input[type=text]:focus,select:focus{outline:none;border-color:var(--acc-dim);box-shadow:0 0 0 2px rgba(232,148,58,.08)}
input[type=text]{width:100%;background:var(--bg);border:1px solid var(--line);color:var(--fg);border-radius:6px;padding:9px 12px;font:inherit;transition:border-color .15s}
button{background:var(--acc);color:#090d12;border:none;border-radius:6px;padding:8px 18px;font-weight:600;cursor:pointer;font-family:inherit;font-size:13px;letter-spacing:.01em;transition:opacity .15s,box-shadow .2s}button.primary{font-weight:700}button:focus-visible{box-shadow:0 0 0 2px var(--bg),0 0 0 4px var(--acc-dim)}
button:hover{opacity:.92;box-shadow:0 0 14px rgba(232,148,58,.12)}
button.ghost{background:transparent;color:var(--acc);border:1px solid var(--acc-dim);font-weight:650}
button:disabled{opacity:.35;cursor:wait}
select{appearance:none;background-color:var(--card);background-image:linear-gradient(45deg,transparent 50%,var(--acc) 50%),linear-gradient(135deg,var(--acc) 50%,transparent 50%);background-position:calc(100% - 15px) 50%,calc(100% - 10px) 50%;background-size:5px 5px,5px 5px;background-repeat:no-repeat;border:1px solid var(--hair);color:var(--fg);border-radius:8px;padding:9px 34px 9px 12px;font:inherit;font-size:13px;min-height:38px}::placeholder{color:var(--mut);opacity:.82}
.mut{color:var(--mut)}.ok{color:var(--ok)}.warn{color:var(--warn)}
.item{background:var(--card);border:1px solid var(--line);border-left:3px solid transparent;border-radius:6px;padding:14px 18px;margin-bottom:10px;cursor:pointer;transition:border-color .12s,border-left-color .2s}
.item:hover{border-color:var(--acc-dim);border-left-color:var(--acc)}.frontier-item{padding:18px 20px;border-left:0}.frontier-top{display:flex;align-items:center;justify-content:space-between;gap:10px}.frontier-item h3{margin:9px 0 5px;font-size:16px;line-height:1.4;color:var(--fg)}.frontier-meta{display:flex;gap:8px;flex-wrap:wrap;margin-top:13px}.frontier-meta span{padding:4px 8px;background:var(--bg);border:1px solid var(--line);border-radius:5px;color:var(--mut);font-size:11px}.frontier-item .row{margin-top:14px}
.small{font-size:12px;color:var(--mut);font-weight:500}.small.ok{color:var(--ok)}.small.warn{color:var(--warn)}
.badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:10.5px;background:var(--bg);color:var(--mut);margin-left:6px;border:1px solid var(--line);letter-spacing:.02em;font-weight:600}
.badge.run{background:#132618;color:var(--ok);border-color:var(--ok)}.badge.err{background:#241414;color:#e87a7a;border-color:#e87a7a}
table{border-collapse:collapse;width:100%;font-size:13px;margin:12px 0}
th,td{border:1px solid var(--line);padding:6px 10px;text-align:left;vertical-align:top}
th{background:var(--bg);color:var(--mut);font-weight:600;font-size:11px;letter-spacing:.04em;text-transform:uppercase}
.brief h2{font-size:16px;margin:24px 0 10px;color:var(--acc);font-weight:600;letter-spacing:.01em}
.brief h3{font-size:14px;margin:18px 0 8px}
.brief table{font-size:12.5px}
.brief a{color:var(--acc)}.brief p{margin:8px 0;line-height:1.7}.brief ul,.brief ol{padding-left:20px;margin:8px 0}.brief li{margin:4px 0}.brief strong{color:var(--fg);font-weight:600}.brief blockquote{border-left:3px solid var(--acc-dim);margin:12px 0;padding:4px 14px;color:var(--mut);font-style:italic}.brief code{background:var(--bg);padding:2px 6px;border-radius:3px;font-size:12.5px;color:var(--acc)}.brief pre{background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:14px 16px;overflow-x:auto;font-size:12.5px;line-height:1.5;margin:10px 0}.brief pre code{background:none;padding:0;color:var(--fg)}
.upd{border:1px solid var(--line);border-radius:6px;padding:12px 14px;margin-bottom:8px}
.upd .arrow{color:var(--mut)}
@keyframes spin{to{transform:rotate(360deg)}}
.spin{display:inline-block;width:12px;height:12px;border:2px solid var(--line);border-top-color:var(--acc);border-radius:50%;animation:spin 1s linear infinite;vertical-align:-2px;margin-right:6px}
.row{display:flex;align-items:center;gap:10px;margin-top:10px;flex-wrap:wrap}
.row input[type=text]{width:auto;flex:1;min-width:140px}
.row .mut{white-space:nowrap}::-webkit-scrollbar{width:6px}::-webkit-scrollbar-track{background:var(--bg)}::-webkit-scrollbar-thumb{background:var(--line);border-radius:3px}::-webkit-scrollbar-thumb:hover{background:var(--mut)}
[data-theme=light]{--card-2:#f0e5d5;--hair:#d5c3aa;--bg:#f7f3ea;--card:#fffdf8;--line:#e5d7c4;--fg:#1b1814;--mut:#6e6255;--acc:#c8752a;--acc-dim:#a85a18;--ok:#356c45;--warn:#a95022}body{position:relative;z-index:1;font-family:"Segoe UI","Microsoft YaHei","Noto Sans SC",Arial,sans-serif;font-weight:450}main,header,.tabs{position:relative;z-index:1}.card,.item{position:relative;z-index:1}
:root{--card-2:#1b2430;--hair:#2a3542;--shadow:0 18px 50px rgba(0,0,0,.18)}
body{background:radial-gradient(circle at 82% -10%,rgba(232,148,58,.08),transparent 30%),var(--bg)}
header{max-width:1120px;margin:0 auto;padding:24px 32px 18px;border-bottom:1px solid var(--hair)}
header h1{font-size:16px;letter-spacing:.02em}
.tabs{max-width:1120px;margin:0 auto;padding:16px 32px 0;overflow-x:auto}
main{max-width:1120px;padding:32px}#frArea{scroll-margin-top:18px}
.card{box-shadow:var(--shadow);border-color:var(--hair)}
.hero{position:relative;overflow:hidden;padding:32px 36px;margin-bottom:24px;background:linear-gradient(135deg,rgba(232,148,58,.12),transparent 48%),var(--card);border:1px solid var(--hair);border-radius:12px}
.hero:after{content:'✦';position:absolute;right:30px;top:16px;color:var(--acc);font-size:76px;opacity:.11;transform:rotate(18deg)}
.eyebrow{font-size:10px;line-height:1.2;color:var(--acc);font-weight:700;letter-spacing:.16em;text-transform:uppercase}
.hero-title{max-width:720px;margin:14px 0 8px;font-size:clamp(26px,4vw,42px);line-height:1.12;letter-spacing:-.035em;font-weight:650}
.hero-copy{max-width:650px;margin:0;color:var(--mut);font-size:15px;line-height:1.75;font-weight:500}
.hero-actions{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:24px}
.stat-strip{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:28px;background:transparent;border:0;overflow:visible}
.stat{position:relative;min-height:104px;padding:17px 18px 14px;background:linear-gradient(145deg,var(--card),var(--card-2));border:1px solid var(--hair);border-radius:10px;box-shadow:0 8px 22px rgba(87,61,31,.07);overflow:hidden}.stat:after{content:"";position:absolute;right:-18px;top:-22px;width:74px;height:74px;border:1px solid var(--acc-dim);border-radius:50%;opacity:.22}.stat strong{display:block;font-size:28px;color:var(--fg);line-height:1;font-weight:750}.stat span{display:block;margin-top:8px;color:var(--fg);font-size:13px;font-weight:700;letter-spacing:.01em}.stat .stat-caption{display:block;margin-top:2px;color:var(--mut);font-size:11px;font-weight:500;letter-spacing:0}
.section-head{display:flex;justify-content:space-between;align-items:end;gap:16px;margin:26px 0 12px}.section-head h2{margin:0;font-size:18px;letter-spacing:-.015em}.section-head p{margin:0;color:var(--mut);font-size:13px;font-weight:500}
.result-head{border-left:3px solid var(--acc)}.result-title{margin:10px 0 4px;font-size:22px;line-height:1.25}.result-meta{display:flex;gap:8px;flex-wrap:wrap;color:var(--mut);font-size:11px}.meta-chip{padding:3px 8px;background:var(--bg);border:1px solid var(--line);border-radius:999px}
.result-layout{display:grid;grid-template-columns:minmax(0,1fr) 238px;gap:20px;align-items:start}.result-layout>.brief{margin:0}.result-aside{display:grid;gap:12px;position:sticky;top:18px}.side-card{padding:17px 17px;background:linear-gradient(145deg,var(--card-2),var(--card));color:var(--fg);border:1px solid var(--hair);border-radius:10px;box-shadow:0 8px 22px rgba(87,61,31,.07)}.side-card p{margin:8px 0 0;color:var(--fg);font-size:12px;line-height:1.7}
.empty{padding:32px 20px;text-align:center;color:var(--mut);border:1px dashed var(--hair);border-radius:10px}.empty-mark{display:block;margin-bottom:7px;color:var(--acc);font-size:20px}.save-confirm{display:flex;align-items:center;gap:12px;width:100%;padding:12px 14px;background:linear-gradient(135deg,#edf7ec,#fbfff8);border:1px solid #a7c6a3;border-radius:9px;color:#294c2f}.save-confirm .save-check{display:grid;place-items:center;width:24px;height:24px;border-radius:50%;background:#4d8b57;color:#fff;font-weight:800}.save-confirm .save-copy{flex:1}.save-confirm .save-copy strong{display:block;font-size:13px}.save-confirm .save-copy span{display:block;margin-top:2px;font-size:12px;color:#4f6d54}.exp-summary{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:0 0 18px}.exp-summary-card{padding:14px 16px;background:var(--card);border:1px solid var(--hair);border-radius:10px}.exp-summary-card strong{display:block;font-size:22px;line-height:1}.exp-summary-card span{display:block;margin-top:5px;color:var(--mut);font-size:11px}.exp-card{padding:18px 20px;background:var(--card);border:1px solid var(--hair);border-radius:11px;margin-bottom:12px;box-shadow:0 6px 18px rgba(87,61,31,.05)}.exp-card.focus{border-color:var(--acc);box-shadow:0 0 0 3px rgba(196,119,43,.12),0 10px 24px rgba(87,61,31,.09)}.exp-top,.exp-meta{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}.exp-index{font-size:12px;color:var(--mut);font-weight:700;letter-spacing:.08em}.exp-card h3{margin:12px 0 7px;font-size:17px;line-height:1.4;color:var(--fg)}.exp-description{margin:0;color:var(--mut);font-size:12px;line-height:1.65}.exp-meta{margin-top:14px;justify-content:flex-start;color:var(--mut);font-size:11px}.path-chip{display:flex;align-items:center;gap:8px;margin-top:12px;padding:9px 11px;background:var(--bg);border:1px solid var(--line);border-radius:7px;color:var(--ok);font:11px/1.5 Consolas,"Microsoft YaHei",monospace;overflow-wrap:anywhere}.file-list{display:flex;gap:6px;flex-wrap:wrap;margin-top:9px}.file-list span{padding:3px 7px;background:var(--card-2);border:1px solid var(--hair);border-radius:5px;color:var(--mut);font-size:10px}.exp-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-top:14px}.exp-feedback{font-size:12px;color:var(--ok)}
button:focus-visible,input:focus-visible,textarea:focus-visible,select:focus-visible{outline:2px solid var(--acc);outline-offset:2px;box-shadow:none}
@media(max-width:760px){header{padding:18px 18px 14px}.tabs{padding:12px 18px 0}main{padding:20px 16px}.hero{padding:24px 22px}.hero-title{font-size:30px}.stat-strip{grid-template-columns:1fr}.result-layout{grid-template-columns:1fr}.result-aside{position:static}.section-head{display:block}.section-head p{margin-top:4px}}
@media(prefers-reduced-motion:reduce){*,*:before,*:after{scroll-behavior:auto!important;transition:none!important;animation:none!important}}
[data-theme=light] body{background:radial-gradient(circle at 82% -10%,rgba(225,156,74,.12),transparent 32%),var(--bg)}
[data-theme=light] .stat-strip{border-color:#b9ad9d;box-shadow:0 8px 22px rgba(87,61,31,.07)}
[data-theme=light] .stat{background:var(--card);color:var(--fg)}
[data-theme=light] .stat strong{font-size:24px;font-weight:750;color:#1d1b18}
[data-theme=light] .stat span{color:var(--mut);font-size:12px;font-weight:600;letter-spacing:.01em}
[data-theme=light] .mut,[data-theme=light] .small{color:var(--mut)}
[data-theme=light] .warn{color:var(--warn);font-weight:600}
[data-theme=light] .hero-title,[data-theme=light] .section-head h2{color:var(--fg)}[data-theme=light] .side-card{background:linear-gradient(145deg,#fff7ea,#f2e7d5);border-color:#d7b98f;box-shadow:0 8px 22px rgba(122,84,36,.09)}[data-theme=light] .side-card p{color:#40372e}[data-theme=light] .side-card .eyebrow{color:#a85d1f}[data-theme=light] .file-list span{background:#f4ecdf}
[data-theme=light] button.primary{color:#2b180a;font-weight:700}
[data-theme=light] .hero{background:linear-gradient(135deg,rgba(225,156,74,.14),transparent 48%),var(--card);box-shadow:0 16px 36px rgba(122,84,36,.08)}
[data-theme=light] .stat{background:var(--card)}
[data-theme=light] .badge.run{background:#e5f0e5;color:#3d6b48;border-color:#8fb193}
[data-theme=light] .badge.err{background:#f8e8e2;color:#9b4c25;border-color:#d59d84}
[data-theme=light] th{background:#f0ebe3}
[data-theme=light] .small.ok{color:var(--ok)}
[data-theme=light] .small.warn{color:var(--warn)}[data-theme=light] button.primary{background:#b5671f;color:#fffaf2;border-color:#b5671f;font-weight:800;text-shadow:0 1px 1px rgba(66,30,8,.18)}[data-theme=light] button.primary:hover{background:#9f5718;color:#fffaf2}[data-theme=light] .stat strong{font-size:30px;font-weight:800;color:#211c16;letter-spacing:-.02em}[data-theme=light] .stat span:not(.stat-caption){color:#211c16;font-size:13px;font-weight:750;letter-spacing:.01em}[data-theme=light] .stat .stat-caption{color:#50483f;font-size:11px;font-weight:600}[data-theme=light]{--card-2:#f0e5d5;--hair:#d5c3aa;--shadow:0 18px 50px rgba(122,84,36,.10)}.theme-toggle{letter-spacing:.02em}.hero,.result-head,.exp-card,.item,.card{transition:background-color .18s ease,border-color .18s ease,box-shadow .18s ease,color .18s ease}.frontier-item{border-left:3px solid var(--acc)}.frontier-item:hover{border-left-color:var(--acc-dim)}.exp-card{border-left:3px solid var(--hair)}.exp-card.focus{border-left-color:var(--acc)}#kArea,#hArea,#pArea{display:grid;gap:12px}#kArea .item,#hArea .item,#pArea .item{border-left:3px solid var(--hair);box-shadow:0 8px 24px rgba(0,0,0,.04)}#kArea .item:hover,#hArea .item:hover,#pArea .item:hover{border-left-color:var(--acc);box-shadow:0 12px 28px rgba(0,0,0,.08)}#hArea .item{position:relative;padding-left:26px}#hArea .item:before{content:"";position:absolute;left:-7px;top:22px;width:9px;height:9px;border-radius:50%;background:var(--acc);box-shadow:0 0 0 4px var(--card)}[data-theme=light] #kArea .item,[data-theme=light] #hArea .item,[data-theme=light] #pArea .item{box-shadow:0 8px 24px rgba(122,84,36,.06)}</style></head><body>
<header><h1>Lodestar（导星）<span class="v" id="ver"></span></h1><button class="ghost theme-toggle" id="themeBtn" onclick="toggleTheme()" style="font-size:11px;padding:5px 11px;margin-left:auto" title="切换浅色/深色主题">☀ 浅色</button></header>
<div class="tabs" id="tabs" role="tablist" aria-label="Lodestar 工作区">
<button data-t="frontier" class="on">选题</button>
<button data-t="research">研究</button>
<button data-t="knowledge">知识库</button>
<button data-t="history">历史</button>
<button data-t="experiment">实验</button>
<button data-t="project">项目</button>
</div>
<main>
<div class="tab on" id="t-frontier"><div class="hero"><div class="eyebrow">AI RESEARCH CONSOLE <span class="badge run">LOCAL WORKSPACE</span></div><h2 class="hero-title">把一个问题，变成一份可追踪的研究简报。</h2><p class="hero-copy">从研究目标出发，沿着来源、证据、洞察和知识更新走完一条完整航迹。</p><div class="hero-actions"><button class="primary" onclick="goResearch()">开始一次研究</button><button id="frBtn" class="ghost">生成本周选题</button><span class="mut small" id="frNote"></span></div><div class="stat-strip"><div class="stat"><strong id="statTasks">—</strong><span>历史研究</span><span class="stat-caption">可追踪的研究轨迹</span></div><div class="stat"><strong id="statKnowledge">—</strong><span>知识概念</span><span class="stat-caption">持续沉淀的认知资产</span></div><div class="stat"><strong id="statProjects">—</strong><span>进行中项目</span><span class="stat-caption">正在推进的实验方向</span></div></div></div><div class="section-head"><div><h2>本周值得研究</h2><p>基于 Knowledge State 和进行中项目，生成 3 个有上下文的方向。</p></div><span class="small">点击选题即可开始</span></div><div id="frArea"></div></div>
<div class="tab" id="t-research">
  <div class="section-head"><div><h2>研究问题</h2><p>写下你真正想知道的事，Lodestar 会保留来源和研究轨迹。</p></div></div>
  <div class="card"><textarea id="goal" placeholder="研究目标，如：研究最近 Agent Memory 有哪些值得关注的新方向"></textarea>
  <div class="row"><button class="primary" id="startBtn">开始研究</button><span class="mut small" id="runNote"></span></div></div>
  <div id="resArea"></div>
</div>
<div class="tab" id="t-knowledge"><div class="section-head"><div><div class="eyebrow">KNOWLEDGE STATE</div><h2>可复用的认知资产</h2><p>把概念、证据与掌握程度放在同一条研究航迹上。</p></div></div>
  <div class="card"><div class="row"><input type="text" id="kq" placeholder="搜索概念…（回车）"><button class="ghost" onclick="loadK()">搜索</button></div>
  <div class="row"><button id="kseedBtn" class="ghost">seed 已知概念</button><input type="text" id="kseed" placeholder="Agent,Skill,Eval…"></div></div>
  <div id="kArea"></div>
  <div class="card" style="margin-top:14px"><div class="row"><button class="primary" id="quizBtn">评估我的掌握</button>
  <span class="mut small" id="quizNote">agent 出题 → 你回答 → 自动更新 Knowledge State</span></div><div id="quizArea"></div></div>
</div>
<div class="tab" id="t-history"><div class="section-head"><div><div class="eyebrow">RESEARCH LOG</div><h2>研究历史</h2><p>每一次研究都留下问题、来源和下一步动作。</p></div></div><div id="hArea"></div></div>
<div class="tab" id="t-experiment"><div class="section-head"><div><div class="eyebrow">EXPERIMENT LAB</div><h2>从研究结论到可运行骨架</h2><p>每个实验都保留假设、来源任务和可复现的 A/B 验证入口。</p></div><button onclick="loadExp()" class="ghost">刷新实验</button></div><div id="eSummary"></div><div id="eArea"></div></div>
<div class="tab" id="t-project"><div class="section-head"><div><div class="eyebrow">PROJECTS</div><h2>进行中的项目</h2><p>把研究问题、代码仓库与实验骨架放在同一处管理。</p></div></div>
  <div class="card"><div class="row"><input type="text" id="purl" placeholder="GitHub 仓库链接，如 https://github.com/xxx/repo">
  <button id="paddBtn" class="ghost">登记项目</button></div>
  <div class="row"><label class="mut small">标记状态（研究只关联「active」）：</label>
  <select id="pstatus"><option value="active">进行中</option><option value="paused">暂停</option><option value="archived">归档</option><option value="idea">想法</option></select></div></div>
  <div id="pArea"></div>
</div>
</main>
<script>
function updateThemeLabel(){const light=document.documentElement.getAttribute("data-theme")==="light";document.querySelector("#themeBtn").textContent=light?"☾ 深色":"☀ 浅色";}function toggleTheme(){const h=document.documentElement;const cur=h.getAttribute("data-theme");const nxt=cur==="light"?"":"light";h.setAttribute("data-theme",nxt);localStorage.setItem("lodestar-theme",nxt);updateThemeLabel();}if(localStorage.getItem("lodestar-theme")==="light"){document.documentElement.setAttribute("data-theme","light")}updateThemeLabel();
const $=s=>document.querySelector(s);
function goResearch(){$('#tabs').querySelector('button[data-t=research]').click();setTimeout(()=>$('#goal').focus(),0);}
async function loadSummary(){try{const s=await jget('/api/summary');$('#statTasks').textContent=s.tasks;$('#statKnowledge').textContent=s.knowledge;$('#statProjects').textContent=s.projects;}catch(e){}}
async function jget(p,t){const c=new AbortController();const to=setTimeout(()=>c.abort(),t||120000);
 const r=await fetch(p,{signal:c.signal});clearTimeout(to);if(!r.ok)throw new Error('HTTP '+r.status);return r.json();}
async function jpost(p,b,t){const c=new AbortController();const to=setTimeout(()=>c.abort(),t||120000);
 const r=await fetch(p,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b||{}),signal:c.signal});
 clearTimeout(to);if(!r.ok)throw new Error('HTTP '+r.status);return r.json();}
// ---- tabs ----
$('#tabs').onclick=e=>{const b=e.target.closest('button');if(!b)return;
 document.querySelectorAll('.tabs button').forEach(x=>x.classList.remove('on'));
 document.querySelectorAll('.tab').forEach(x=>x.classList.remove('on'));
 b.classList.add('on');$('#t-'+b.dataset.t).classList.add('on');
 if(b.dataset.t==='knowledge')loadK(); if(b.dataset.t==='history')loadH(); if(b.dataset.t==='experiment')loadExp(); if(b.dataset.t==='project')loadProjects();};
jget('/api/health').then(h=>$('#ver').textContent='v'+h.version); loadSummary();
// ---- md 渲染（够用）----
function md(t){if(!t)return'';t=t.replace(/&/g,'&amp;').replace(/</g,'&lt;');
 t=t.replace(/^### (.*)$/gm,'<h3>$1</h3>').replace(/^## (.*)$/gm,'<h2>$1</h2>').replace(/^# (.*)$/gm,'<h2>$1</h2>');
 t=t.replace(/\*\*(.*?)\*\*/g,'<b>$1</b>').replace(/\*([^*]+)\*/g,'<i>$1</i>');
 t=t.replace(/\[([^\]]+)\]\((https?:[^)]+)\)/g,'<a href="$2" target="_blank">$1</a>');
 t=t.replace(/^\|(.+)\|$/gm,(m,row)=>{const c=row.split('|').slice(1,-1);return c.every(x=>/^:?-{2,}:?$/.test(x.trim()))?'': '<tr>'+c.map(x=>'<td>'+x.trim()+'</td>').join('')+'</tr>';});
 t=t.replace(/<tr>\s*<\/tr>/g,''); t=t.replace(/((?:<tr>.*?<\/tr>\s*)+)/g,'<table><tbody>$1</tbody></table>');
 t=t.replace(/^(?!<)((?:[^<][^\n]*)$)/gm,'<p>$1</p>');
 return t.replace(/\n\n/g,'\n');}
function esc(s){return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;');}
// ---- 研究 ----
async function startResearch(){const g=$('#goal').value.trim();if(!g){$('#runNote').innerHTML='<span class="warn">先写下一个研究问题。</span>';return;}
 $('#runNote').innerHTML='<span class="spin"></span>研究启动中…';
 try{const r=await jpost('/api/research',{goal:g});$('#runNote').innerHTML='';if(r.error){$('#runNote').innerHTML='<span class="warn">'+esc(r.error)+'</span>';return;}pollTask(r.task_id);}
 catch(e){$('#runNote').innerHTML='<span class="warn">请求失败：'+esc(e.message||e)+'</span>';}
}
$('#startBtn').onclick=startResearch;
function researchTopic(t){$('#goal').value=t;startResearch();$('#tabs').querySelector('button[data-t=research]').click();}
let pv=0;
async function pollTask(id){$('#runNote').innerHTML='<span class="spin"></span>研究中…';
 const iv=setInterval(async()=>{const d=await jget('/api/task/'+id);
  if(d.task&&(d.task.status==='finished'||d.task.status==='error')){clearInterval(iv);$('#runNote').innerHTML='';
   renderTask(d);loadSummary();pv=0;}else{$('#runNote').innerHTML='<span class="spin"></span>研究中…（'+(++pv)+'s · 步骤 '+(d.trace?d.trace.length:0)+'）';}},2000);}
function renderTask(d){const t=d.task,b=d.brief_md,upd=d.updates.filter(u=>u.status==='pending');
 const next=upd.length?'先确认知识更新，再把结论沉淀进 Knowledge State。':'可以继续从本次 Brief 的 Project Opportunities 创建一个实验。';
 let h='<div class="card result-head"><div class="eyebrow">RESEARCH RUN <span class="badge">'+esc(t.status)+'</span></div><h2 class="result-title">'+esc(t.goal)+'</h2><div class="result-meta"><span class="meta-chip">task '+t.id+'</span><span class="meta-chip">'+esc(t.status)+'</span></div>';
 if(t.metrics&&t.metrics.degraded){h+='<p class="warn">⚠️ 实时研究失败，以下为示例数据（mock 降级）。</p>';}
 else if(t.status==='error'){h+='<p class="warn">'+esc((t.metrics||{}).error||'执行失败')+'</p>';}
 if(upd.length){h+='<h4>待应用的知识更新</h4>'+upd.map(u=>{
   const p=u.proposal;return '<label class="upd" style="display:block;cursor:pointer"><input type="checkbox" class="updbox" value="'+u.id+'" checked> '+
   '<b>'+esc(u.concept)+'</b> <span class="arrow">'+(p.old_status?esc(p.old_status)+'/':'')+(p.old_confidence?esc(p.old_confidence)+' → ':'')+
   esc(p.new_status)+'/'+esc(p.new_confidence)+'</span><br><span class="small">'+esc(p.claim||'')+'</span></label>';}).join('')+
   '<div class="row"><button class="primary" onclick="applyChecked(\''+t.id+'\')">应用选中的更新</button></div>';}
 if(t.status==='finished'&&d.opportunities&&d.opportunities.length){
  h+='<div id="experimentSaveRow" class="row experiment-save-row" style="margin-top:10px"><select id="oppSel">'+d.opportunities.map((o,i)=>'<option value="'+(i+1)+'">'+(i+1)+'. '+esc(o.slice(0,46))+'…</option>').join('')+'</select>'+
  '<button class="ghost" onclick="saveExpPick(\''+t.id+'\')">存为实验</button></div>';}
 h+='</div><div class="result-layout"><div class="brief card">'+md(b)+'</div><aside class="result-aside"><div class="side-card"><div class="eyebrow">NEXT MOVE</div><p>'+esc(next)+'</p></div><div class="side-card"><div class="eyebrow">TRACE</div><p>每一步检索、阅读和判断都会保存在这次研究的轨迹里。</p></div></aside></div>';$('#resArea').innerHTML=h;}
function applyChecked(id){const ids=[...document.querySelectorAll('.updbox:checked')].map(x=>+x.value);
 if(!ids.length){alert('请先勾选要应用的更新');return;}
 jpost('/api/task/apply',{task_id:id,update_ids:ids}).then(r=>{alert('已应用 '+r.count+' 条更新');pollTask(id);});}
function saveExpPick(id){const pick=+$("#oppSel").value;const row=$("#experimentSaveRow");if(!row)return;row.insertAdjacentHTML("beforeend","<span class=\"small\">保存中…</span>");jpost("/api/experiment/save",{task_id:id,pick}).then(r=>{if(r.error){row.innerHTML="<span class=\"warn\">"+esc(r.error)+"</span>";return;}row.innerHTML="<div class=\"save-confirm\"><span class=\"save-check\">✓</span><div class=\"save-copy\"><strong>已保存为实验 #"+r.exp_id+"</strong><span>"+esc(r.hypothesis||"")+"</span></div><button class=\"primary\" onclick=\"openExperiment("+r.exp_id+")\">查看实验</button></div>";}).catch(e=>{row.innerHTML="<span class=\"warn\">保存失败："+esc(e.message||e)+"</span>";});}
function openExperiment(id){document.querySelector("#tabs button[data-t=experiment]").click();setTimeout(()=>loadExp(id),0);}
async function loadExp(focusId){const d=await jget("/api/experiments");const built=d.filter(e=>e.build_status==="built").length;const draft=d.length-built;document.querySelector("#eSummary").innerHTML=`<div class="exp-summary"><div class="exp-summary-card"><strong>${d.length}</strong><span>全部实验</span></div><div class="exp-summary-card"><strong>${built}</strong><span>已生成骨架</span></div><div class="exp-summary-card"><strong>${draft}</strong><span>待验证假设</span></div></div>`;document.querySelector("#eArea").innerHTML=(d.map(e=>{const files=e.files||[];const fileHtml=files.length?`<div class="file-list">${files.map(f=>`<span>${esc(f)}</span>`).join("")}</div>`:"";const path=e.output_dir?`<div class="path-chip">骨架目录 · ${esc(e.output_dir)}</div>`:"";const taskLink=e.task_id?`<button class="ghost" onclick="openTask(&quot;${esc(e.task_id)}&quot;)">查看研究</button>`:"";return `<article class="exp-card ${focusId==e.id?"focus":""}" id="exp-${e.id}"><div class="exp-top"><div><span class="exp-index">EXPERIMENT #${e.id}</span> <span class="badge ${e.build_status==="built"?"run":e.build_status==="failed"?"err":""}">${esc(e.build_status)}</span></div><span class="small">${esc(e.created_at||"")}</span></div><h3>${esc(e.hypothesis||"未命名实验")}</h3><p class="exp-description">${esc(e.description||"从研究 Brief 提取的可验证假设，等待生成 A/B 骨架。")}</p><div class="exp-meta"><span>来源任务 ${esc(e.task_id||"—")}</span><span>${e.build_status==="built"?"已具备可运行目录":"下一步：生成骨架并运行 eval.py"}</span></div>${path}${fileHtml}<div class="exp-actions">${taskLink}<button class="primary" onclick="buildExp(${e.id})">${e.build_status==="built"?"重新生成骨架":"生成骨架"}</button><span class="exp-feedback" id="exp-feedback-${e.id}"></span></div></article>`}).join(""))||`<div class="empty"><span class="empty-mark">✦</span>还没有实验，从研究 Brief 的 Project Opportunities 保存一个假设吧。</div>`;if(focusId){const node=document.querySelector("#exp-"+focusId);if(node){node.scrollIntoView({behavior:"smooth",block:"center"});}}}
async function buildExp(id){const feedback=document.querySelector("#exp-feedback-"+id);if(feedback)feedback.textContent="正在生成骨架…";try{const r=await jpost("/api/experiment/build",{exp_id:id});await loadExp(id);const done=document.querySelector("#exp-feedback-"+id);if(done)done.innerHTML=r.project?"<span class=\"ok\">骨架已生成，目录已更新</span>":"<span class=\"warn\">"+esc(r.error||"生成失败")+"</span>";}catch(e){const done=document.querySelector("#exp-feedback-"+id);if(done)done.innerHTML="<span class=\"warn\">生成失败："+esc(e.message||e)+"</span>";}}
// ---- 选题 ----
const demoFrontier=[{topic:"Lodestar 如何把 Research Trace 变成 Skill Promotion",priority:"high",label:"能力晋升",related_projects:["Marina-016/lodestar"],why:"把一次研究里的证据、判断和 Eval 结果，沉淀成下一次可以复用的 Skill。",deliverable:"Skill Promotion 证据面板",signal:"Trace · Eval · Knowledge"},{topic:"Agent Memory 如何减少下一次研究的重复检索",priority:"medium",label:"记忆更新",related_projects:["Marina-016/lodestar"],why:"从 partial 概念开始补齐可引用上下文，让下一次研究更快进入真正的新问题。",deliverable:"Memory 更新策略与回归集",signal:"Recall · Confidence · Reuse"},{topic:"Weekly Frontier 如何直接驱动 Experiment",priority:"high",label:"研究闭环",related_projects:["Lodestar / Skill Evaluation Lab"],why:"把选题、Brief、可验证假设和骨架目录串成一条适合演示的产品主线。",deliverable:"一键生成 A/B 实验骨架",signal:"Brief · Hypothesis · Build"}];
function renderDemoFrontier(items){document.querySelector("#frArea").innerHTML=items.map(s=>{const t=esc(s.topic).replace(/"/g,"&quot;");const project=(s.related_projects||[]).map(esc).join(" · ");return `<div class="item frontier-item"><div class="frontier-top"><span class="eyebrow">${esc(s.label||"RESEARCH DIRECTION")}</span><span class="badge">${esc(s.priority||"medium")}</span></div><h3>${esc(s.topic)}</h3><div class="small">${esc(s.why||"")}</div><div class="frontier-meta"><span>${esc(s.deliverable||"研究 Brief")}</span><span>${esc(s.signal||project)}</span></div><div class="row"><button class="ghost" onclick="researchTopic(&quot;${t}&quot;)">研究这条</button><button class="ghost" onclick="useTopic(&quot;${t}&quot;)">填到研究框</button></div></div>`}).join("");}
renderDemoFrontier(demoFrontier);$('#frBtn').onclick=async()=>{$('#frNote').innerHTML='<span class="spin"></span>生成中…';
 try{
  const r=await jpost('/api/frontier',{},60000);$('#frNote').innerHTML='';
  if(r.error){$('#frNote').innerHTML='<span class="warn">'+esc(r.error)+'</span>';}
  if(r.suggestions&&r.suggestions.length){renderDemoFrontier(r.suggestions);document.querySelector("#frArea").scrollIntoView({behavior:"smooth",block:"start"});}
  else{$('#frArea').innerHTML='<p class="warn">（无选题返回）</p>';}
 }catch(e){$('#frNote').innerHTML='<span class="warn">请求失败：'+esc(e.message||e)+'</span>';}};
function useTopic(t){$('#goal').value=t;$('#tabs').querySelector('button[data-t=research]').click();}
// ---- 知识评估（Quiz）----
let quiz={concepts:[],idx:0,q:''};
$('#quizBtn').onclick=quizStart;
async function quizStart(){$('#quizNote').innerHTML='<span class="spin"></span>出题中…';
 const r=await jpost('/api/quiz/start',{},60000);$('#quizNote').innerHTML='';
 if(r.error){$('#quizNote').innerHTML='<span class="warn">'+esc(r.error)+'</span>';return;}
 quiz={concepts:r.concepts,idx:0,q:r.question};showQuizQ(r.concept,r.question);}
function showQuizQ(c,q){quiz.q=q;
 $('#quizArea').innerHTML='<div class="card"><b>'+esc(c)+'</b>：'+esc(q)+
 '<br><textarea id="quizA" placeholder="你的回答…" style="margin-top:8px"></textarea>'+
 '<div class="row"><button class="primary" onclick="quizSubmit()">提交评估</button><span class="mut small" id="quizV"></span></div></div>';}
async function quizSubmit(){const a=$('#quizA').value.trim();if(!a){alert('先写点回答');return;}
 const c=quiz.concepts[quiz.idx];$('#quizV').innerHTML='<span class="spin"></span>评估中…';
 const r=await jpost('/api/quiz/answer',{concept:c,question:quiz.q,answer:a},60000);
 if(r.error){$('#quizV').innerHTML='<span class="warn">'+esc(r.error)+'</span>';return;}
 $('#quizV').innerHTML='<span class="ok">'+r.status+'/'+r.confidence+'</span> '+esc(r.feedback||'');
 if(r.status!=='known'){$('#quizV').innerHTML+=' <button class="ghost" onclick="researchConcept(\''+esc(c).replace(/'/g,"\\'")+'\')">研究这个薄弱点</button>';}
 if(r.next_question){showQuizQ(c,r.next_question);}else{advanceQuiz();}}
function researchConcept(t){$('#goal').value='研究 '+t+' 的最新进展，加深理解';startResearch();$('#tabs').querySelector('button[data-t=research]').click();}
async function advanceQuiz(){quiz.idx++;
 if(quiz.idx>=quiz.concepts.length){$('#quizArea').innerHTML='<p class="ok">本轮评估完成（'+quiz.concepts.length+' 个概念），Knowledge State 已更新。</p>';return;}
 $('#quizV').innerHTML='<span class="spin"></span>下一题…';
 const r=await jpost('/api/quiz/next',{concepts:quiz.concepts,index:quiz.idx},60000);
 if(r.error){$('#quizV').innerHTML='<span class="warn">'+esc(r.error)+'</span>';return;}
 showQuizQ(r.concept,r.question);}
// ---- 项目 ----
$('#paddBtn').onclick=projectAdd;
async function loadProjects(){const d=await jget('/api/projects');
 $('#pArea').innerHTML=(d.map(p=>'<div class="item"><b>'+esc(p.name)+'</b> <span class="badge '+(p.status==='active'?'run':'')+'">'+p.status+'</span>'+
 (p.url?' <a href="'+esc(p.url)+'" target="_blank" style="color:var(--acc)">GitHub</a>':'')+
 '<div class="small">'+esc((p.description||'').slice(0,80))+'</div>'+
 '<div class="small">技术栈: '+esc((p.tech_stack||[]).slice(0,8).join(', '))+'</div>'+
 '<div class="row" style="margin:6px 0 0"><select onchange="projectStatus('+p.id+',this.value)">'+
 ['active','paused','archived','idea'].map(s=>'<option value="'+s+'"'+(p.status===s?' selected':'')+'>'+s+'</option>').join('')+
 '</select></div></div>').join(''))||'<div class="empty"><span class="empty-mark">✦</span>还没有项目，登记一个 GitHub 仓库试试。</div>';}
async function projectAdd(){const url=$('#purl').value.trim();if(!url){alert('先填 GitHub 链接');return;}
 const r=await jpost('/api/project/add',{url:url,status:$('#pstatus').value},90000);
 if(r.error){alert(r.error);return;}alert('已登记 '+r.name+'（技术栈: '+(r.tech_stack||[]).join(', ')+'）');loadProjects();}
function projectStatus(id,s){jpost('/api/project/status',{id:id,status:s}).then(loadProjects);}
// ---- 知识库 ----
async function loadK(){const q=$('#kq').value.trim();const d=await jget('/api/knowledge'+(q?'?q='+encodeURIComponent(q):''));
 $('#kArea').innerHTML=(d.map(c=>'<div class="item"><b>'+esc(c.name)+'</b><span class="badge">'+c.status+'/'+c.confidence+'</span>'+
  (c.notes&&c.notes.length?'<div class="small">'+esc(c.notes.slice(-2).join('；'))+'</div>':'')+'</div>').join(''))||'<div class="empty"><span class="empty-mark">✦</span>还没有知识概念，先 seed 一组你熟悉的主题。</div>';}
$('#kq').onkeydown=e=>{if(e.key==='Enter')loadK();};
$('#kseedBtn').onclick=async()=>{const v=$('#kseed').value.trim();if(!v)return;
 const r=await jpost('/api/knowledge/seed',{names:v});loadK();};
// ---- 历史 ----
async function loadH(){const d=await jget('/api/tasks');
 $('#hArea').innerHTML=d.map(t=>'<div class="item" onclick="openTask(\''+t.id+'\')"><b>'+esc((t.goal||'').slice(0,70))+'</b>'+
 '<span class="badge '+(t.status==='running'?'run':t.status==='error'?'err':'')+'">'+t.status+'</span>'+
 '<div class="small">'+t.created_at+'</div></div>').join('')||'<div class="empty"><span class="empty-mark">✦</span>还没有研究记录，从首页开始一次研究。</div>';}
async function openTask(id){const d=await jget('/api/task/'+id);renderTask(d);$('#tabs').querySelector('button[data-t=research]').click();}
</script></body></html>"""
