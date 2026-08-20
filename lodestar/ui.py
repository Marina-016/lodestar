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
                return self._send(200, repo.list_experiments(ws.conn))
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
                                            description="(UI 保存)")
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
:root{--bg:#0a0e14;--card:#111620;--line:#1e2736;--fg:#e0e4ec;--mut:#6b7280;--acc:#e8943a;--acc-dim:#a06830;--ok:#5b8c5a;--warn:#c0784a}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.65 "Inter","Segoe UI",system-ui,sans-serif;letter-spacing:-.002em}
header{display:flex;align-items:baseline;gap:12px;padding:20px 28px 0}
header h1{font-size:15px;margin:0;font-weight:600;color:var(--fg);letter-spacing:.01em}
header .v{color:var(--mut);font-size:11px;font-weight:400}
.tabs{display:flex;gap:0;padding:22px 28px 0;border-bottom:1px solid var(--line)}
.tabs button{position:relative;background:none;border:none;color:var(--mut);font-size:14px;padding:10px 0;margin-right:28px;cursor:pointer;letter-spacing:.02em;transition:color .2s,transform .15s}
.tabs button::after{content:'';position:absolute;left:0;right:0;bottom:-1px;height:2px;background:linear-gradient(90deg,transparent,var(--acc),transparent);opacity:0;transition:opacity .25s;border-radius:2px}
.tabs button:hover{color:var(--fg);transform:translateY(-1px)}
.tabs button.on{color:var(--acc);font-weight:600;transform:none}
.tabs button.on::after{opacity:1}
main{padding:28px;max-width:960px;margin:0 auto}
.tab{display:none}.tab.on{display:block}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:20px 24px;margin-bottom:18px}
textarea{width:100%;min-height:72px;background:var(--bg);border:1px solid var(--line);color:var(--fg);border-radius:6px;padding:12px 14px;font:inherit;resize:vertical;transition:border-color .15s}
textarea:focus,input[type=text]:focus{outline:none;border-color:var(--acc-dim)}
input[type=text]{width:100%;background:var(--bg);border:1px solid var(--line);color:var(--fg);border-radius:6px;padding:9px 12px;font:inherit;transition:border-color .15s}
button{background:var(--acc);color:#090d12;border:none;border-radius:6px;padding:8px 18px;font-weight:600;cursor:pointer;font-size:13px;letter-spacing:.01em;transition:opacity .15s}
button:hover{opacity:.88}
button.ghost{background:transparent;color:var(--acc);border:1px solid var(--acc-dim);font-weight:500}
button:disabled{opacity:.35;cursor:wait}
select{background:var(--bg);border:1px solid var(--line);color:var(--fg);border-radius:6px;padding:7px 10px;font:inherit;font-size:13px}
.mut{color:var(--mut)}.ok{color:var(--ok)}.warn{color:var(--warn)}
.item{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:14px 18px;margin-bottom:10px;cursor:pointer;transition:border-color .12s}
.item:hover{border-color:var(--acc-dim)}
.small{font-size:12px;color:var(--mut)}
.badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:10.5px;background:var(--bg);color:var(--mut);margin-left:6px;border:1px solid var(--line);letter-spacing:.02em}
.badge.run{background:#132618;color:var(--ok);border-color:var(--ok)}.badge.err{background:#241414;color:#e87a7a;border-color:#e87a7a}
table{border-collapse:collapse;width:100%;font-size:13px;margin:12px 0}
th,td{border:1px solid var(--line);padding:6px 10px;text-align:left;vertical-align:top}
th{background:var(--bg);color:var(--mut);font-weight:600;font-size:11px;letter-spacing:.04em;text-transform:uppercase}
.brief h2{font-size:16px;margin:24px 0 10px;color:var(--acc);font-weight:600;letter-spacing:.01em}
.brief h3{font-size:14px;margin:18px 0 8px}
.brief table{font-size:12.5px}
.brief a{color:var(--acc)}
.upd{border:1px solid var(--line);border-radius:6px;padding:12px 14px;margin-bottom:8px}
.upd .arrow{color:var(--mut)}
@keyframes spin{to{transform:rotate(360deg)}}
.spin{display:inline-block;width:12px;height:12px;border:2px solid var(--line);border-top-color:var(--acc);border-radius:50%;animation:spin 1s linear infinite;vertical-align:-2px;margin-right:6px}
.row{display:flex;align-items:center;gap:10px;margin-top:10px;flex-wrap:wrap}
.row input[type=text]{width:auto;flex:1;min-width:140px}
.row .mut{white-space:nowrap}
</style></head><body>
<header><h1>Lodestar（导星）<span class="v" id="ver"></span></h1></header>
<div class="tabs" id="tabs">
<button data-t="frontier" class="on">选题</button>
<button data-t="research">研究</button>
<button data-t="knowledge">知识库</button>
<button data-t="history">历史</button>
<button data-t="experiment">实验</button>
<button data-t="project">项目</button>
</div>
<main>
<div class="tab on" id="t-frontier"><div class="row"><button id="frBtn">生成本周选题</button><span class="mut small" id="frNote"></span></div><div id="frArea"></div></div>
<div class="tab" id="t-research">
  <div class="card"><textarea id="goal" placeholder="研究目标，如：研究最近 Agent Memory 有哪些值得关注的新方向"></textarea>
  <div class="row"><button id="startBtn">开始研究</button><span class="mut small" id="runNote"></span></div></div>
  <div id="resArea"></div>
</div>
<div class="tab" id="t-knowledge">
  <div class="card"><div class="row"><input type="text" id="kq" placeholder="搜索概念…（回车）"><button class="ghost" onclick="loadK()">搜索</button></div>
  <div class="row"><button id="kseedBtn" class="ghost">seed 已知概念</button><input type="text" id="kseed" placeholder="Agent,Skill,Eval…"></div></div>
  <div id="kArea"></div>
  <div class="card" style="margin-top:14px"><div class="row"><button id="quizBtn">评估我的掌握</button>
  <span class="mut small">agent 出题 → 你回答 → 自动更新 Knowledge State</span></div><div id="quizArea"></div></div>
</div>
<div class="tab" id="t-history"><div id="hArea"></div></div>
<div class="tab" id="t-experiment"><button onclick="loadExp()" class="ghost">刷新</button><div id="eArea"></div></div>
<div class="tab" id="t-project">
  <div class="card"><div class="row"><input type="text" id="purl" placeholder="GitHub 仓库链接，如 https://github.com/xxx/repo">
  <button id="paddBtn" class="ghost">登记项目</button></div>
  <div class="row"><label class="mut small">标记状态（研究只关联「active」）：</label>
  <select id="pstatus"><option value="active">进行中</option><option value="paused">暂停</option><option value="archived">归档</option><option value="idea">想法</option></select></div></div>
  <div id="pArea"></div>
</div>
</main>
<script>
const $=s=>document.querySelector(s);
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
jget('/api/health').then(h=>$('#ver').textContent='v'+h.version);
// ---- md 渲染（够用）----
function md(t){if(!t)return'';t=t.replace(/&/g,'&amp;').replace(/</g,'&lt;');
 t=t.replace(/^### (.*)$/gm,'<h3>$1</h3>').replace(/^## (.*)$/gm,'<h2>$1</h2>').replace(/^# (.*)$/gm,'<h2>$1</h2>');
 t=t.replace(/\*\*(.*?)\*\*/g,'<b>$1</b>').replace(/\*([^*]+)\*/g,'<i>$1</i>');
 t=t.replace(/\[([^\]]+)\]\((https?:[^)]+)\)/g,'<a href="$2" target="_blank">$1</a>');
 t=t.replace(/^\|(.+)\|$/gm,(m,row)=>{const c=row.split('|').slice(1,-1);return c.every(x=>/^:?-{2,}:?$/.test(x.trim()))?'': '<tr>'+c.map(x=>'<td>'+x.trim()+'</td>').join('')+'</tr>';});
 t=t.replace(/<tr>\s*<\/tr>/g,'');
 t=t.replace(/^(?!<)((?:[^<][^\n]*)$)/gm,'<p>$1</p>');
 return t.replace(/\n\n/g,'\n');}
function esc(s){return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;');}
// ---- 研究 ----
async function startResearch(){const g=$('#goal').value.trim();if(!g)return;
 $('#runNote').innerHTML='<span class="spin"></span>研究启动中…';const r=await jpost('/api/research',{goal:g});
 $('#runNote').innerHTML='';if(r.error){$('#runNote').innerHTML='<span class="warn">'+esc(r.error)+'</span>';return;}
 pollTask(r.task_id);}
$('#startBtn').onclick=startResearch;
function researchTopic(t){$('#goal').value=t;startResearch();$('#tabs').querySelector('button[data-t=research]').click();}
let pv=0;
async function pollTask(id){$('#runNote').innerHTML='<span class="spin"></span>研究中…';
 const iv=setInterval(async()=>{const d=await jget('/api/task/'+id);
  if(d.task&&(d.task.status==='finished'||d.task.status==='error')){clearInterval(iv);$('#runNote').innerHTML='';
   renderTask(d);pv=0;}else{$('#runNote').innerHTML='<span class="spin"></span>研究中…（'+(++pv)+'s · 步骤 '+(d.trace?d.trace.length:0)+'）';}},2000);}
function renderTask(d){const t=d.task,b=d.brief_md,upd=d.updates.filter(u=>u.status==='pending');
 let h='<div class="card"><h3 style="margin:0 0 6px">'+esc(t.goal)+'</h3><span class="mut small">task '+t.id+' · '+t.status+'</span>';
 if(t.metrics&&t.metrics.degraded){h+='<p class="warn">⚠️ 实时研究失败，以下为示例数据（mock 降级）。</p>';}
 else if(t.status==='error'){h+='<p class="warn">'+esc((t.metrics||{}).error||'执行失败')+'</p>';}
 if(upd.length){h+='<h4>待应用的知识更新</h4>'+upd.map(u=>{
   const p=u.proposal;return '<label class="upd" style="display:block;cursor:pointer"><input type="checkbox" class="updbox" value="'+u.id+'" checked> '+
   '<b>'+esc(u.concept)+'</b> <span class="arrow">'+(p.old_status?esc(p.old_status)+'/':'')+(p.old_confidence?esc(p.old_confidence)+' → ':'')+
   esc(p.new_status)+'/'+esc(p.new_confidence)+'</span><br><span class="small">'+esc(p.claim||'')+'</span></label>';}).join('')+
   '<div class="row"><button onclick="applyChecked(\''+t.id+'\')">应用选中的更新</button></div>';}
 if(t.status==='finished'&&d.opportunities&&d.opportunities.length){
  h+='<div class="row" style="margin-top:10px"><select id="oppSel">'+d.opportunities.map((o,i)=>'<option value="'+(i+1)+'">'+(i+1)+'. '+esc(o.slice(0,46))+'…</option>').join('')+'</select>'+
  '<button class="ghost" onclick="saveExpPick(\''+t.id+'\')">存为实验</button></div>';}
 h+='</div><div class="brief">'+md(b)+'</div>';$('#resArea').innerHTML=h;}
function applyChecked(id){const ids=[...document.querySelectorAll('.updbox:checked')].map(x=>+x.value);
 if(!ids.length){alert('请先勾选要应用的更新');return;}
 jpost('/api/task/apply',{task_id:id,update_ids:ids}).then(r=>{alert('已应用 '+r.count+' 条更新');pollTask(id);});}
function saveExpPick(id){const pick=+$('#oppSel').value;
 jpost('/api/experiment/save',{task_id:id,pick}).then(r=>{alert(r.exp_id?('已保存为实验 #'+r.exp_id):(r.error||'保存失败'));});}
async function loadExp(){const d=await jget('/api/experiments');
 $('#eArea').innerHTML=(d.map(e=>'<div class="item"><b>#'+e.id+'</b> <span class="badge '+(e.build_status==='built'?'run':e.build_status==='failed'?'err':'')+'">'+e.build_status+'</span>'+
 '<div>'+esc((e.hypothesis||'').slice(0,80))+'</div>'+
 '<div class="small">task '+esc(e.task_id||'-')+'</div>'+
 (e.output_dir?'<div class="small ok">'+esc(e.output_dir)+'</div>':'')+
 '<div class="row" style="margin:6px 0 0"><button class="ghost" onclick="buildExp('+e.id+')">生成骨架</button></div></div>').join(''))||'<p class="mut">（无实验）</p>';}
async function buildExp(id){const r=await jpost('/api/experiment/build',{exp_id:id});
 alert(r.project?('骨架已生成：'+r.project):(r.error||'生成失败'));loadExp();}
// ---- 选题 ----
$('#frBtn').onclick=async()=>{$('#frNote').innerHTML='<span class="spin"></span>生成中…';
 try{
  const r=await jpost('/api/frontier',{},60000);$('#frNote').innerHTML='';
  if(r.error){$('#frNote').innerHTML='<span class="warn">'+esc(r.error)+'</span>';}
  if(r.suggestions&&r.suggestions.length){$('#frArea').innerHTML=r.suggestions.map((s,i)=>{const t=esc(s.topic).replace(/'/g,"\\'");
   return '<div class="item"><b>'+esc(s.topic)+'</b> <span class="badge">'+s.priority+'</span>'+
   (s.related_projects&&s.related_projects.length?' <span class="badge run">'+s.related_projects.map(esc).join('、')+'</span>':'')+
   '<div class="small">'+esc(s.why)+'</div>'+
   '<div class="row" style="margin:6px 0 0"><button class="ghost" onclick="researchTopic(\''+t+'\')">研究这条</button>'+
   '<button class="ghost" onclick="useTopic(\''+t+'\')">填到研究框</button></div></div>';}).join('');}
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
 '<div class="row"><button onclick="quizSubmit()">提交评估</button><span class="mut small" id="quizV"></span></div></div>';}
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
 '</select></div></div>').join(''))||'<p class="mut">（无项目）登记一个 GitHub 仓库试试</p>';}
async function projectAdd(){const url=$('#purl').value.trim();if(!url){alert('先填 GitHub 链接');return;}
 const r=await jpost('/api/project/add',{url:url,status:$('#pstatus').value},90000);
 if(r.error){alert(r.error);return;}alert('已登记 '+r.name+'（技术栈: '+(r.tech_stack||[]).join(', ')+'）');loadProjects();}
function projectStatus(id,s){jpost('/api/project/status',{id:id,status:s}).then(loadProjects);}
// ---- 知识库 ----
async function loadK(){const q=$('#kq').value.trim();const d=await jget('/api/knowledge'+(q?'?q='+encodeURIComponent(q):''));
 $('#kArea').innerHTML=(d.map(c=>'<div class="item"><b>'+esc(c.name)+'</b><span class="badge">'+c.status+'/'+c.confidence+'</span>'+
  (c.notes&&c.notes.length?'<div class="small">'+esc(c.notes.slice(-2).join('；'))+'</div>':'')+'</div>').join(''))||'<p class="mut">（空）</p>';}
$('#kq').onkeydown=e=>{if(e.key==='Enter')loadK();};
$('#kseedBtn').onclick=async()=>{const v=$('#kseed').value.trim();if(!v)return;
 const r=await jpost('/api/knowledge/seed',{names:v});loadK();};
// ---- 历史 ----
async function loadH(){const d=await jget('/api/tasks');
 $('#hArea').innerHTML=d.map(t=>'<div class="item" onclick="openTask(\''+t.id+'\')"><b>'+esc((t.goal||'').slice(0,70))+'</b>'+
 '<span class="badge '+(t.status==='running'?'run':t.status==='error'?'err':'')+'">'+t.status+'</span>'+
 '<div class="small">'+t.created_at+'</div></div>').join('')||'<p class="mut">（无任务）</p>';}
async function openTask(id){const d=await jget('/api/task/'+id);renderTask(d);$('#tabs').querySelector('button[data-t=research]').click();}
</script></body></html>"""
