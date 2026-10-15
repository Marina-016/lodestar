"""Local-only HTTP adapter; model calls happen only after an explicit send."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import parse_qs, urlparse
import uuid
import webbrowser

from lodestar.agent.conversation import ConversationAgent
from lodestar.chat_settings import configure, snapshot
from lodestar.config import load_config
from lodestar.context import Workspace
from lodestar.llm import LLMClient, LLMError
from lodestar.memory import learning, repo
from lodestar.providers.capabilities import apply_reasoning, reasoning_choices
from lodestar.web import memory

STATIC = Path(__file__).parent / 'static'
SCHEMA = '''
CREATE TABLE IF NOT EXISTS web_items (
 id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL,
 conversation_id TEXT NOT NULL, settings TEXT NOT NULL DEFAULT '{}',
 status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS web_runs (
 id TEXT PRIMARY KEY, item_id TEXT NOT NULL, request_key TEXT NOT NULL,
 status TEXT NOT NULL, message TEXT NOT NULL, config TEXT NOT NULL,
 result TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 UNIQUE(item_id,request_key)
);
CREATE UNIQUE INDEX IF NOT EXISTS web_one_active_run ON web_runs(item_id)
 WHERE status IN ('queued','running','cancelling');
CREATE TABLE IF NOT EXISTS web_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS web_notes (
 id TEXT PRIMARY KEY, item_id TEXT NOT NULL, content TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'active', revision INTEGER NOT NULL DEFAULT 1,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
'''


Conflict = memory.MemoryBusy


class WebApp:
    """An isolated Web database, without migrating or merging existing CLI history."""
    def __init__(self, config, *, agent_factory=ConversationAgent, llm_factory=LLMClient):
        self.config = replace(config, demo_replay=False)
        self.agent_factory, self.llm_factory = agent_factory, llm_factory
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.RLock()
        self.catalog_cache = None
        self.cancel_signals = {}
        with self.workspace() as ws:
            ws.conn.executescript(SCHEMA)
            memory.initialize(ws)
            index = ws.conn.execute("SELECT sql FROM sqlite_master WHERE name='web_one_active_run'").fetchone()[0]
            if 'cancelling' not in index:
                ws.conn.execute('DROP INDEX web_one_active_run')
                ws.conn.execute("CREATE UNIQUE INDEX web_one_active_run ON web_runs(item_id) WHERE status IN ('queued','running','cancelling')")
            ws.conn.execute("UPDATE web_runs SET status='interrupted' WHERE status IN ('queued','running','cancelling')")
            if not ws.conn.execute("SELECT 1 FROM web_items WHERE id='main'").fetchone():
                self._create(ws, 'main', '主对话')
            ws.conn.commit()

    @contextmanager
    def workspace(self, config=None):
        ws = Workspace(config or self.config)
        try:
            yield ws
        finally:
            ws.close()

    def _create(self, ws, item_id, name, project_id=None, parent_id=None, dispatch_key=None):
        session = ConversationAgent(ws, None).start(user_id=self.user(item_id), project_id=project_id)
        ws.conn.execute('INSERT INTO web_items(id,owner,name,conversation_id,project_id,parent_id,dispatch_key) VALUES(?,?,?,?,?,?,?)',
                        (item_id, 'local', name, session, project_id, parent_id, dispatch_key))
        return item_id

    @staticmethod
    def user(item_id=None):
        return memory.USER

    def item(self, ws, item_id):
        row = ws.conn.execute('SELECT * FROM web_items WHERE id=? AND owner=?', (item_id, 'local')).fetchone()
        if row is None:
            raise LookupError('找不到这个空间')
        return dict(row)

    def config_for(self, item):
        settings = json.loads(item['settings'])
        allowed = {k: v for k, v in settings.items() if k in {'model', 'profile', 'thinking'}}
        if allowed.get('profile') == 'custom':
            allowed.pop('profile')
        cfg = configure(self.config, **allowed)
        if 'reasoning' in settings:
            cfg = apply_reasoning(cfg, settings['reasoning'])
        if 'enabled' in settings:
            cfg = replace(cfg, model_calls_disabled=not settings['enabled'])
        return cfg

    def state(self, item_id='main'):
        with self.workspace() as ws:
            item = self.item(ws, item_id)
            cfg = self.config_for(item)
            rows = ws.conn.execute('SELECT * FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT 200',
                                   (item['conversation_id'],)).fetchall()
            messages = [{**dict(r), 'metadata': json.loads(r['metadata'])} for r in reversed(rows)]
            run = ws.conn.execute('SELECT * FROM web_runs WHERE item_id=? ORDER BY rowid DESC LIMIT 1', (item_id,)).fetchone()
            notes = [dict(r) for r in ws.conn.execute('SELECT * FROM assistant_memories WHERE user_id=? ORDER BY updated_at DESC,id', (self.user(),))]
            return {'item': item, 'items': self.task_list(ws),
                    'messages': messages, 'run': dict(run) if run else None, 'notes': notes,
                    'context_notes': memory.notes(ws, item, active_only=True),
                    'learning': learning.profile(ws.conn, user_id=self.user(item_id)),
                    'projects': repo.list_projects(ws.conn), 'configuration': snapshot(cfg),
                    'models': list(dict.fromkeys([cfg.model, *cfg.model_profiles.values()])),
                    'reasoning': reasoning_choices(cfg, include_current=True),
                    'enabled': not cfg.model_calls_disabled, 'mode': cfg.llm_mode}

    def models(self, item_id):
        # An explicit directory request may run while inference is paused. It only
        # reads model IDs; no completion or chargeable search is triggered here.
        with self.workspace() as ws:
            cfg = self.config_for(self.item(ws, item_id))
        with self.lock:
            if self.catalog_cache and time.monotonic() - self.catalog_cache[0] < 60:
                ids = self.catalog_cache[1]
            else:
                client = self.llm_factory(replace(cfg, model_calls_disabled=False))
                try:
                    ids = client.list_models()
                finally:
                    client.close()
                self.catalog_cache = (time.monotonic(), ids)
        ids = list(dict.fromkeys([cfg.model, *ids, *cfg.model_profiles.values()]))
        return {'models': ids, 'reasoning': {model: reasoning_choices(
                    replace(cfg, model=model, llm_thinking=False, llm_thinking_mode='default'))
                    for model in ids}}

    def task_list(self, ws):
        rows = ws.conn.execute("SELECT i.*,r.status AS run_status,r.id AS run_id,(SELECT data FROM web_events WHERE run_id=r.id AND kind='progress' ORDER BY id DESC LIMIT 1) AS progress FROM web_items i LEFT JOIN web_runs r ON r.rowid=(SELECT MAX(rowid) FROM web_runs WHERE item_id=i.id) WHERE i.owner='local' ORDER BY i.created_at,i.id")
        return [dict(r) for r in rows]

    def project(self, data):
        name, description = data.get('name', ''), data.get('description', '')
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80 or not isinstance(description, str) or len(description) > 4000:
            raise ValueError('项目信息无效')
        with self.lock, self.workspace() as ws:
            if ws.conn.execute('SELECT 1 FROM projects WHERE name=?', (name.strip(),)).fetchone():
                raise Conflict('已存在同名项目，请选择已有项目')
            return repo.upsert_project(ws.conn, name.strip(), description=description.strip(), status='active')

    def create(self, name, project_id=None, *, parent_id=None, dispatch_key=None):
        name = str(name).strip()[:80]
        if not name:
            raise ValueError('请输入任务名称')
        with self.lock, self.workspace() as ws, ws.conn:
            if project_id is not None and (type(project_id) is not int or not ws.conn.execute('SELECT 1 FROM projects WHERE id=?', (project_id,)).fetchone()):
                raise ValueError('项目无效')
            if dispatch_key:
                previous = ws.conn.execute('SELECT id FROM web_items WHERE dispatch_key=?', (dispatch_key,)).fetchone()
                if previous:
                    return previous['id']
            return self._create(ws, uuid.uuid4().hex[:12], name, project_id, parent_id, dispatch_key)

    def update(self, item_id, data):
        with self.lock, self.workspace() as ws, ws.conn:
            item = self.item(ws, item_id)
            if 'project_id' in data:
                project_id = data['project_id']
                if project_id is not None and (type(project_id) is not int or not ws.conn.execute('SELECT 1 FROM projects WHERE id=?', (project_id,)).fetchone()):
                    raise ValueError('项目无效')
                memory.invalidate(ws, [item])
                ws.conn.execute('UPDATE web_items SET project_id=? WHERE id=?', (project_id, item_id))
                ws.conn.execute('UPDATE agent_sessions SET project_id=? WHERE conversation_id=?', (project_id, item['conversation_id']))
            if 'settings' in data:
                settings = data['settings']
                if not isinstance(settings, dict) or set(settings) - {'model','profile','thinking','reasoning','enabled'}:
                    raise ValueError('模型设置无效')
                if 'enabled' in settings and not isinstance(settings['enabled'], bool):
                    raise ValueError('调用开关无效')
                previous = json.loads(item['settings'])
                if settings.get('reasoning') == 'current' and settings.get('model', self.config.model) != previous.get('model', self.config.model):
                    raise ValueError('切换模型后请重新选择推理强度')
                item['settings'] = json.dumps({**previous, **settings})
                self.config_for(item)
                ws.conn.execute('UPDATE web_items SET settings=? WHERE id=?', (item['settings'], item_id))
            if 'name' in data:
                name = str(data['name']).strip()[:80]
                if not name:
                    raise ValueError('请输入任务名称')
                ws.conn.execute('UPDATE web_items SET name=? WHERE id=?', (name, item_id))
            if 'status' in data:
                if item_id == 'main' or data['status'] not in ('active','completed','archived'):
                    raise ValueError('状态无效')
                ws.conn.execute('UPDATE web_items SET status=? WHERE id=?', (data['status'], item_id))

    def note(self, item_id, data):
        with self.lock, self.workspace() as ws, ws.conn:
            return memory.edit(ws, self.item(ws, item_id), data, catalog=True)

    def learning(self, data):
        with self.lock, self.workspace() as ws, ws.conn:
            memory.edit_learning(ws, data)

    def submit(self, item_id, message, key, *, background=False, policy=None):
        if not isinstance(message, str) or not message.strip() or len(message) > 24000:
            raise ValueError('请输入 1–24000 字的问题')
        if not isinstance(key, str) or not 1 <= len(key) <= 100:
            raise ValueError('提交标识无效')
        with self.lock, self.workspace() as ws, ws.conn:
            item = self.item(ws, item_id)
            existing = ws.conn.execute('SELECT id FROM web_runs WHERE item_id=? AND request_key=?', (item_id,key)).fetchone()
            if existing:
                return existing['id']
            if item['status'] != 'active':
                raise Conflict('请先恢复此任务')
            if ws.conn.execute("SELECT 1 FROM web_runs WHERE item_id=? AND status IN ('queued','running','cancelling')", (item_id,)).fetchone():
                raise Conflict('当前空间正在回答，请稍后再发送')
            cfg = self.config_for(item)
            if cfg.model_calls_disabled and cfg.llm_mode != 'mock':
                raise Conflict('模型调用已暂停，请在模型设置中启用后发送')
            run_id = uuid.uuid4().hex
            ws.conn.execute('INSERT INTO web_runs(id,item_id,request_key,status,message,config,policy) VALUES(?,?,?,?,?,?,?)',
                            (run_id,item_id,key,'queued',message,json.dumps(snapshot(cfg)),json.dumps(policy or {},ensure_ascii=False)))
            signal = self.cancel_signals[run_id] = threading.Event()
        threading.Thread(target=self.execute, args=(run_id,item,cfg,message,signal,background), daemon=True).start()
        return run_id

    def cancel(self, item_id, run_id):
        if not isinstance(run_id, str) or not run_id:
            raise ValueError('运行标识无效')
        with self.lock, self.workspace() as ws, ws.conn:
            self.item(ws, item_id)
            row = ws.conn.execute('SELECT status FROM web_runs WHERE id=? AND item_id=?', (run_id,item_id)).fetchone()
            if row is None:
                raise LookupError('找不到这次运行')
            if row['status'] in ('queued','running'):
                ws.conn.execute("UPDATE web_runs SET status='cancelling' WHERE id=?", (run_id,))
                signal = self.cancel_signals.get(run_id)
                if signal:
                    signal.set()
                return 'cancelling'
            return row['status']

    def event(self, ws, run_id, kind, data):
        with self.lock, ws.conn:
            inserted = ws.conn.execute("INSERT INTO web_events(run_id,kind,data) SELECT ?,?,? WHERE EXISTS (SELECT 1 FROM web_runs WHERE id=? AND status='running')",
                (run_id,kind,json.dumps(data,ensure_ascii=False),run_id))
            if not inserted.rowcount:
                raise LLMError('Run is no longer accepting events', code='cancelled', role='conversation')

    def record_action(self, ws, run_id, receipt):
        """Host-only facts, retained even if cancellation follows a committed effect.

        Call in the mutation's transaction when possible. Never store memory text
        here: forgetting a memory must not leave another copy in action history.
        """
        data = json.dumps(receipt, ensure_ascii=False, sort_keys=True)
        ws.conn.execute("INSERT INTO web_events(run_id,kind,data) SELECT ?,'action',? WHERE EXISTS "
            "(SELECT 1 FROM web_runs WHERE id=?) AND NOT EXISTS "
            "(SELECT 1 FROM web_events WHERE run_id=? AND kind='action' AND data=?)",
            (run_id,data,run_id,run_id,data))

    @staticmethod
    def action_receipts(ws, run_id):
        return [json.loads(r['data']) for r in ws.conn.execute(
            "SELECT data FROM web_events WHERE run_id=? AND kind='action' ORDER BY id", (run_id,))]

    def execute(self, run_id, item, cfg, message, signal, background=False):
        def check_cancel():
            if signal.is_set():
                raise LLMError('Run cancelled by user', code='cancelled', role='conversation')

        with self.workspace(cfg) as ws:
            llm = None
            try:
                with self.lock, ws.conn:
                    row = ws.conn.execute('SELECT status,policy FROM web_runs WHERE id=?', (run_id,)).fetchone()
                    if row['status'] not in ('queued','cancelling'):
                        return
                    ws.conn.execute("UPDATE web_runs SET status='running' WHERE id=? AND status='queued'", (run_id,))
                policy = json.loads(row['policy'])
                # A queued cancellation still records its input and stopped answer,
                # but never constructs a provider client or starts inference.
                llm = None if signal.is_set() else self.llm_factory(cfg)
                from lodestar.memory.assistant import prompt_rows, select_rows
                from lodestar.web.tools import assistant_tools
                note_rows = select_rows(memory.notes(ws, item, active_only=True), message)
                notes = prompt_rows(note_rows)
                result = self.agent_factory(ws,llm).turn(item['conversation_id'],message,
                    user_id=self.user(item['id']), memory_notes=notes, check_cancel=check_cancel,
                    history_after=item['context_after'], track_exposure=not background,
                    execution_policy=policy,
                    assistant_tools=assistant_tools(self, item, run_id, message, check_cancel, user_authored=not background, execution_policy=policy),
                    on_text=lambda text: self.event(ws,run_id,'text',text),
                    on_progress=lambda action: self.event(ws,run_id,'progress',action))
                scope = {'item_id': item['id'], 'project_id': item['project_id'], 'user_id': self.user(),
                         'history_after': item['context_after'], 'background': background, 'execution_policy': policy,
                         'memory': [{'id': r['id'], 'scope': r['scope'], 'revision': r['revision']} for r in note_rows]}
                result['web_context'] = scope
                result['action_receipts'] = self.action_receipts(ws, run_id)
                last = ws.conn.execute("SELECT id,metadata FROM messages WHERE conversation_id=? AND role='assistant' ORDER BY id DESC LIMIT 1", (item['conversation_id'],)).fetchone()
                if last:
                    metadata = {**json.loads(last['metadata']), 'web_context': scope, 'run_id': run_id,
                                'action_receipts': result['action_receipts']}
                    with ws.conn:
                        ws.conn.execute('UPDATE messages SET metadata=? WHERE id=?', (json.dumps(metadata,ensure_ascii=False),last['id']))
                with self.lock, ws.conn:
                    status = 'error' if result.get('status') == 'error' else 'completed'
                    if signal.is_set() or result.get('status') == 'cancelled':
                        status = 'cancelled'
                    ws.conn.execute("UPDATE web_runs SET status=?, result=? WHERE id=? AND status IN ('running','cancelling')",
                        (status,json.dumps(result,ensure_ascii=False),run_id))
            except Exception:
                # Provider bodies and credentials must never become browser diagnostics.
                with self.lock, ws.conn:
                    status = 'cancelled' if signal.is_set() else 'error'
                    answer = '本轮已停止，运行记录已保留。' if signal.is_set() else '本轮未完成，记录已保留。请检查本地服务配置后重试。'
                    receipts = self.action_receipts(ws, run_id)
                    last = ws.conn.execute('SELECT role FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT 1',
                                           (item['conversation_id'],)).fetchone()
                    if last and last['role'] == 'user':
                        repo.add_message(ws.conn, item['conversation_id'], 'assistant', answer,
                                         metadata={'run_id': run_id, 'status': status, 'action_receipts': receipts})
                    ws.conn.execute("UPDATE web_runs SET status=?,result=? WHERE id=? AND status IN ('queued','running','cancelling')",
                        (status,json.dumps({'answer':answer,'action_receipts':receipts},ensure_ascii=False),run_id))
            finally:
                with self.lock:
                    self.cancel_signals.pop(run_id, None)
                if llm is not None:
                    llm.close()

    def events(self, run_id, after):
        with self.workspace() as ws:
            row = ws.conn.execute('SELECT r.* FROM web_runs r JOIN web_items i ON i.id=r.item_id WHERE r.id=? AND i.owner=?', (run_id,'local')).fetchone()
            if row is None:
                raise LookupError('找不到这次运行')
            events = [{'id':r['id'],'kind':r['kind'],'data':json.loads(r['data'])} for r in ws.conn.execute('SELECT * FROM web_events WHERE run_id=? AND id>? ORDER BY id LIMIT 500', (run_id,after))]
            return {'run':dict(row),'events':events}


def make_server(app, port=8123):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def respond(self, data, status=200, mime='application/json; charset=utf-8'):
            body = json.dumps(data,ensure_ascii=False).encode() if mime.startswith('application/json') else data
            self.send_response(status)
            self.send_header('Content-Type',mime)
            self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Referrer-Policy','same-origin')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def dispatch(self, write=False):
            expected = {f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}
            if self.headers.get('Host') not in expected:
                return self.respond({'error':'仅支持本机访问'},403)
            if write and (self.headers.get('X-Lodestar-Token') != app.token or self.headers.get('Origin') not in (None,*('http://'+h for h in expected))):
                return self.respond({'error':'请从本地工作空间提交'},403)
            parsed = urlparse(self.path)
            path, query = parsed.path, parse_qs(parsed.query)
            try:
                if not write:
                    if path == '/':
                        return self.respond((STATIC/'index.html').read_text(encoding='utf-8').replace('__TOKEN__',app.token).encode(),mime='text/html; charset=utf-8')
                    if path in ('/app.mjs','/markdown.mjs','/sources.mjs','/actions.mjs','/style.css','/icon.svg'):
                        mime = 'text/css' if path.endswith('.css') else 'image/svg+xml' if path.endswith('.svg') else 'text/javascript'
                        return self.respond((STATIC/path[1:]).read_bytes(),mime=mime+'; charset=utf-8')
                    if path == '/api/state':
                        return self.respond(app.state(query.get('item',['main'])[0]))
                    if path == '/api/tasks':
                        with app.workspace() as ws:
                            return self.respond({'items': app.task_list(ws)})
                    if path == '/api/events':
                        return self.respond(app.events(query.get('run',[''])[0],int(query.get('after',['0'])[0])))
                    return self.respond({'error':'页面不存在'},404)
                length = int(self.headers.get('Content-Length','0'))
                if not 0 < length <= 100000:
                    return self.respond({'error':'请求大小无效'},413)
                data = json.loads(self.rfile.read(length))
                if not isinstance(data,dict):
                    raise ValueError('请求格式无效')
                if path == '/api/items':
                    return self.respond({'id':app.create(data.get('name',''), data.get('project_id'))},201)
                if path == '/api/projects':
                    return self.respond({'id': app.project(data)},201)
                item_id = data.get('item','main')
                if not isinstance(item_id,str):
                    raise ValueError('空间标识无效')
                if path == '/api/models':
                    return self.respond(app.models(item_id))
                if path == '/api/reasoning':
                    model = data.get('model', '')
                    if not isinstance(model, str):
                        raise ValueError('模型无效')
                    cfg = configure(app.config, model=model)
                    return self.respond(reasoning_choices(replace(cfg, llm_thinking=False, llm_thinking_mode='default')))
                if path == '/api/item':
                    app.update(item_id,data)
                    return self.respond(app.state(item_id))
                elif path == '/api/notes':
                    app.note(item_id,data)
                elif path == '/api/learning':
                    app.learning(data)
                elif path == '/api/cancel':
                    return self.respond({'status':app.cancel(item_id,data.get('run'))})
                elif path == '/api/send':
                    return self.respond({'id':app.submit(item_id,data.get('message'),data.get('key'))},202)
                else:
                    return self.respond({'error':'接口不存在'},404)
                return self.respond({'ok':True})
            except (Conflict, memory.MemoryBusy) as error:
                return self.respond({'error':str(error)},409)
            except LookupError as error:
                return self.respond({'error':str(error)},404)
            except (ValueError,TypeError):
                return self.respond({'error':'请求参数无效，请检查输入与模型设置'},400)
            except Exception:
                return self.respond({'error':'本地服务暂时无法完成此操作'},500)

        def do_GET(self):
            self.dispatch()

        def do_POST(self):
            self.dispatch(True)

    return ThreadingHTTPServer(('127.0.0.1',port),Handler)


def serve(port=8123, open_browser=True, config=None):
    cfg = config or load_config()
    root = Path(__file__).resolve().parents[2] / 'workspace' / 'web'
    cfg = replace(cfg,db_path=root/'web.db',workspace_dir=root,demo_replay=False)
    server = make_server(WebApp(cfg),port)
    url = f'http://127.0.0.1:{server.server_port}'
    print(f'Lodestar 本地工作空间: {url}',flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8123)
    parser.add_argument('--no-browser',action='store_true')
    args = parser.parse_args()
    serve(args.port,not args.no_browser)
