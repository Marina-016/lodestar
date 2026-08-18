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
from lodestar.frontier import generate_frontier
from lodestar.llm import LLMClient
from lodestar.memory import repo

_cfg = None
_runners: dict = {}  # task_id -> Thread


def _ws() -> Workspace:
    return Workspace(load_config())


# ----------------------------------------------------------------------
# 后台研究
# ----------------------------------------------------------------------
def _run_research(task_id: str, goal: str, cfg) -> None:
    ws = Workspace(cfg)
    try:
        ResearchAgent(ws, interactive=False).run(goal, apply_updates="pending", task_id=task_id)
    except Exception as e:  # noqa: BLE001
        try:
            repo.finish_task(ws.conn, task_id, "", status="error", metrics={"error": str(e)})
        except Exception:  # noqa: BLE001
            pass
    finally:
        ws.close()
        _runners.pop(task_id, None)


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
            ws = _ws()
            try:
                pend = repo.list_knowledge_updates(ws.conn, task_id=task_id, status="pending")
                applied = []
                for u in pend:
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
                ctx = repo.list_concepts(ws.conn)
                recent = [dict(r) for r in ws.conn.execute(
                    "SELECT goal, created_at FROM research_tasks WHERE status='finished' ORDER BY created_at DESC LIMIT 5"
                ).fetchall()]
                llm = LLMClient(load_config())
                report = generate_frontier(load_config(), llm, ctx, recent)
                return self._send(200, report)
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
:root{--bg:#0f1420;--card:#171e2e;--line:#27324a;--fg:#e6ecf7;--mut:#8b9bb4;--acc:#5aa9ff;--ok:#3fbf6f;--warn:#ffb04d}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.6 "Segoe UI",system-ui,sans-serif}
header{display:flex;align-items:center;gap:16px;padding:14px 20px;border-bottom:1px solid var(--line);background:var(--card)}
header h1{font-size:17px;margin:0;font-weight:600}
header .v{color:var(--mut);font-size:12px}
.tabs{display:flex;gap:4px;padding:10px 20px;border-bottom:1px solid var(--line)}
.tabs button{background:none;border:none;color:var(--mut);font-size:14px;padding:6px 14px;cursor:pointer;border-radius:6px}
.tabs button.on{color:var(--fg);background:#22304a;font-weight:600}
main{padding:18px 20px;max-width:980px;margin:0 auto}
.tab{display:none}.tab.on{display:block}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:14px}
textarea{width:100%;min-height:70px;background:#0b101b;border:1px solid var(--line);color:var(--fg);border-radius:8px;padding:10px;font:inherit;resize:vertical}
input[type=text]{width:100%;background:#0b101b;border:1px solid var(--line);color:var(--fg);border-radius:8px;padding:8px 10px}
button{background:var(--acc);color:#06121f;border:none;border-radius:8px;padding:8px 18px;font-weight:600;cursor:pointer;font-size:14px}
button.ghost{background:none;color:var(--acc);border:1px solid var(--acc);font-weight:400}
button:disabled{opacity:.5;cursor:wait}
.mut{color:var(--mut)} .ok{color:var(--ok)} .warn{color:var(--warn)}
.item{border:1px solid var(--line);border-radius:8px;padding:10px 12px;margin-bottom:8px;cursor:pointer}
.item:hover{border-color:var(--acc)}
.small{font-size:12px;color:var(--mut)}
.badge{display:inline-block;padding:1px 8px;border-radius:10px;font-size:11px;background:#22304a;color:var(--mut);margin-left:6px}
.badge.run{background:#2a3a1e;color:var(--ok)} .badge.err{background:#3a1e1e;color:#ff7b7b}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{border:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
th{background:#22304a;color:var(--mut);font-weight:600}
.brief h2{font-size:16px;margin:18px 0 8px;color:var(--acc)}
.brief h3{font-size:15px;margin:16px 0 6px}
.brief table{font-size:12.5px}
.brief a{color:var(--acc)}
.upd{border:1px solid var(--line);border-radius:8px;padding:10px;margin-bottom:8px}
.upd .arrow{color:var(--mut)}
@keyframes spin{to{transform:rotate(360deg)}}
.spin{display:inline-block;width:12px;height:12px;border:2px solid var(--mut);border-top-color:var(--acc);border-radius:50%;animation:spin 1s linear infinite;vertical-align:-2px;margin-right:6px}
</style></head><body>
<header><h1>Lodestar（导星）<span class="v" id="ver"></span></h1></header>
<div class="tabs" id="tabs">
<button data-t="research" class="on">研究</button>
<button data-t="frontier">选题</button>
<button data-t="knowledge">知识库</button>
<button data-t="history">历史</button>
</div>
<main>
<div class="tab on" id="t-research">
  <div class="card"><textarea id="goal" placeholder="研究目标，如：研究最近 Agent Memory 有哪些值得关注的新方向"></textarea>
  <br><button id="startBtn">开始研究</button> <span class="mut small" id="runNote"></span></div>
  <div id="resArea"></div>
</div>
<div class="tab" id="t-frontier"><button id="frBtn">生成本周选题</button><span class="mut small" id="frNote"></span><div id="frArea"></div></div>
<div class="tab" id="t-knowledge">
  <div class="card"><input type="text" id="kq" placeholder="搜索概念…（回车）">&nbsp;
  <button id="kseedBtn" class="ghost">seed 已知概念</button><input type="text" id="kseed" placeholder="Agent,Skill,Eval…" style="width:240px;display:inline-block;margin-left:8px"></div>
  <div id="kArea"></div>
</div>
<div class="tab" id="t-history"><div id="hArea"></div></div>
</main>
<script>
const $=s=>document.querySelector(s), api={g:p=>fetch(p).then(r=>r.json()),p:(p,b)=>fetch(p,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b||{})}).then(r=>r.json())};
// ---- tabs ----
$('#tabs').onclick=e=>{const b=e.target.closest('button');if(!b)return;
 document.querySelectorAll('.tabs button').forEach(x=>x.classList.remove('on'));
 document.querySelectorAll('.tab').forEach(x=>x.classList.remove('on'));
 b.classList.add('on');$('#t-'+b.dataset.t).classList.add('on');
 if(b.dataset.t==='knowledge')loadK(); if(b.dataset.t==='history')loadH();};
api.g('/api/health').then(h=>$('#ver').textContent='v'+h.version);
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
$('#startBtn').onclick=async()=>{const g=$('#goal').value.trim();if(!g)return;
 $('#runNote').innerHTML='<span class="spin"></span>研究启动中…';const r=await api.p('/api/research',{goal:g});
 $('#runNote').innerHTML='';pollTask(r.task_id);};
let pv=0;
async function pollTask(id){$('#runNote').innerHTML='<span class="spin"></span>研究中…';
 const iv=setInterval(async()=>{const d=await api.g('/api/task/'+id);
  if(d.task&&(d.task.status==='finished'||d.task.status==='error')){clearInterval(iv);$('#runNote').innerHTML='';
   renderTask(d);pv=0;}else{$('#runNote').textContent='研究中…（'+(++pv)+'s）';}},2000);}
function renderTask(d){const t=d.task,b=d.brief_md,upd=d.updates.filter(u=>u.status==='pending');
 let h='<div class="card"><h3 style="margin:0 0 6px">'+esc(t.goal)+'</h3><span class="mut small">task '+t.id+' · '+t.status+'</span>';
 if(t.status==='error'){h+='<p class="warn">'+esc((t.metrics||{}).error||'执行失败')+'</p>';}
 if(upd.length){h+='<h4>待应用的知识更新（'+upd.length+'）</h4>'+upd.map(u=>{
   const p=u.proposal;return '<div class="upd"><b>'+esc(u.concept)+'</b> <span class="arrow">'+
   (p.old_status?esc(p.old_status)+'/':'')+(p.old_confidence?esc(p.old_confidence)+' → ':'')+
   esc(p.new_status)+'/'+esc(p.new_confidence)+'</span><br><span class="small">'+esc(p.claim||'')+'</span></div>';}).join('')+
   '<button onclick="applyUpd(\''+t.id+'\')">应用这些知识更新</button>';}
 h+='</div><div class="brief">'+md(b)+'</div>';$('#resArea').innerHTML=h;}
async function applyUpd(id){await api.p('/api/task/apply',{task_id:id});pollTask(id);}
// ---- 选题 ----
$('#frBtn').onclick=async()=>{$('#frNote').innerHTML='<span class="spin"></span>生成中…';
 const r=await api.p('/api/frontier');$('#frNote').innerHTML='';
 $('#frArea').innerHTML=r.suggestions.map((s,i)=>'<div class="item" onclick="useTopic(\''+esc(s.topic).replace(/'/g,"\\'")+'\')">'+
 '<b>'+esc(s.topic)+'</b> <span class="badge">'+s.priority+'</span><br><span class="small">'+esc(s.why)+'</span></div>').join('');};
function useTopic(t){$('#goal').value=t;$('#tabs').querySelector('button[data-t=research]').click();}
// ---- 知识库 ----
async function loadK(){const q=$('#kq').value.trim();const d=await api.g('/api/knowledge'+(q?'?q='+encodeURIComponent(q):''));
 $('#kArea').innerHTML=(d.map(c=>'<div class="item"><b>'+esc(c.name)+'</b><span class="badge">'+c.status+'/'+c.confidence+'</span>'+
  (c.notes&&c.notes.length?'<div class="small">'+esc(c.notes.slice(-2).join('；'))+'</div>':'')+'</div>').join(''))||'<p class="mut">（空）</p>';}
$('#kq').onkeydown=e=>{if(e.key==='Enter')loadK();};
$('#kseedBtn').onclick=async()=>{const v=$('#kseed').value.trim();if(!v)return;
 const r=await api.p('/api/knowledge/seed',{names:v});loadK();};
// ---- 历史 ----
async function loadH(){const d=await api.g('/api/tasks');
 $('#hArea').innerHTML=d.map(t=>'<div class="item" onclick="openTask(\''+t.id+'\')"><b>'+esc((t.goal||'').slice(0,70))+'</b>'+
 '<span class="badge '+(t.status==='running'?'run':t.status==='error'?'err':'')+'">'+t.status+'</span>'+
 '<div class="small">'+t.created_at+'</div></div>').join('')||'<p class="mut">（无任务）</p>';}
async function openTask(id){const d=await api.g('/api/task/'+id);renderTask(d);$('#tabs').querySelector('button[data-t=research]').click();}
</script></body></html>"""
