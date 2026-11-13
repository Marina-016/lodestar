"""Local-only HTTP adapter; model calls happen only after an explicit send."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import os
import socket
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
from lodestar.web import memory, model_health
from lodestar.web import projects
from lodestar.project_sources import fetch_snapshot, RepositoryError
from lodestar import project_workspaces
from lodestar.web.maintenance import backup_database, workspace_lease

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
 retry_user_message_id INTEGER, continue_message_id INTEGER,
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
        self.workers = {}
        self.project_operations = {}
        self.project_workers = set()
        self.closing = False
        self.instance_id=uuid.uuid4().hex
        self.scheduler=None
        with self.workspace() as ws, ws.conn:
            ws.conn.executescript(SCHEMA)
            ws.conn.executescript(model_health.SCHEMA)
            memory.initialize(ws)
            projects.initialize(ws)
            from lodestar.web import paper_hub
            paper_hub.initialize(ws)
            from lodestar.web import interruptions
            interruptions.initialize(ws)
            ws.conn.execute("UPDATE web_followups SET status='interrupted',error='服务已重启，补充内容保留，请重新发送' WHERE status IN ('pending','starting')")
            from lodestar.web.background import notify
            for pending in ws.conn.execute("SELECT id,project_id FROM web_research_jobs WHERE status IN ('queued','running')").fetchall():
                notify(ws.conn,'research:'+pending['id'],'research',pending['project_id'],'interrupted')
            for pending in ws.conn.execute("SELECT id,item_id FROM web_runs WHERE status IN ('queued','running','cancelling')").fetchall():
                notify(ws.conn,'run:'+pending['id'],'task',pending['item_id'],'interrupted')
            ws.conn.execute("UPDATE web_research_jobs SET status='interrupted' WHERE status IN ('queued','running')")
            index = ws.conn.execute("SELECT sql FROM sqlite_master WHERE name='web_one_active_run'").fetchone()[0]
            if 'cancelling' not in index:
                ws.conn.execute('DROP INDEX web_one_active_run')
                ws.conn.execute("CREATE UNIQUE INDEX web_one_active_run ON web_runs(item_id) WHERE status IN ('queued','running','cancelling')")
            ws.conn.execute("UPDATE web_runs SET status='interrupted' WHERE status IN ('queued','running','cancelling')")
            from lodestar.web.recovery import recover_latest_interrupted
            recover_latest_interrupted(ws.conn)
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
        if item_id != 'main' and parent_id is None:
            main = ws.conn.execute("SELECT settings FROM web_items WHERE id='main'").fetchone()
            if main:
                preferences = {k:v for k,v in json.loads(main[0]).items()
                               if k in {'model','profile','thinking','reasoning'}}
                ws.conn.execute('UPDATE web_items SET settings=? WHERE id=?',
                                (json.dumps(preferences),item_id))
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

    @staticmethod
    def _source_preview(value):
        """Keep a source's identity in navigation state, never its saved body."""
        if not isinstance(value, dict):
            return {}
        fields = ('url', 'title', 'date', 'venue', 'read_depth', 'read_query',
                  'record_url', 'related_paper_url', 'authors', 'year', 'source_type',
                  'provider', 'authority', 'dynamic_source', 'retrieved_at')
        return {key: value[key] for key in fields if value.get(key) not in (None, '', [], {})}

    @classmethod
    def _metadata_preview(cls, metadata):
        """A bounded projection for the main conversation response.

        The complete provenance stays in SQLite and is fetched only when the
        user opens a reply's source/context panel. Sending the full saved body
        of every tool read with every navigation makes switching tasks laggy.
        """
        preview = {key: metadata[key] for key in ('reference_text', 'model_configuration', 'action_receipts',
                                                   'run_id', 'status', 'model_error', 'partial_answer', 'suggested_prompts',
                                                   'paper_focus', 'referenced_papers', 'error_message', 'timing')
                   if key in metadata}
        candidates = [cls._source_preview(source) for source in metadata.get('candidates', [])]
        details = [cls._source_preview(source) for source in metadata.get('source_details', [])]
        if candidates:
            preview['candidates'] = [source for source in candidates if source]
        if details:
            preview['source_details'] = [source for source in details if source]
        events = []
        for event in metadata.get('dialogue_events', []):
            if not isinstance(event, dict):
                continue
            result = event.get('result') if isinstance(event.get('result'), dict) else {}
            params = event.get('params') if isinstance(event.get('params'), dict) else {}
            item = {'action': event.get('action'),
                    'params': {key: params[key] for key in ('url', 'query') if key in params},
                    'result': {key: result[key] for key in ('error', 'record_url', 'read_depth') if key in result}}
            sources = [cls._source_preview(source) for source in result.get('sources', [])]
            if sources:
                item['result']['sources'] = [source for source in sources if source]
            if item['action'] or item['params'] or item['result']:
                events.append(item)
        if events:
            preview['dialogue_events'] = events
        preview['inspection_available'] = bool({'candidates', 'source_details', 'dialogue_events', 'web_context'} & metadata.keys())
        return preview

    def _message_view(self, row, *, compact=False):
        value = dict(row)
        metadata = json.loads(value['metadata'])
        value['metadata'] = self._metadata_preview(metadata) if compact else metadata
        return value

    def message_inspection(self, item_id, message_id):
        """Return one reply's owned provenance plus only its preceding window."""
        if type(message_id) is not int or message_id < 1:
            raise ValueError('消息标识无效')
        with self.workspace() as ws:
            item = self.item(ws, item_id)
            rows = ws.conn.execute('''SELECT * FROM messages WHERE conversation_id=? AND id<=? AND coalesce(kind,'')<>'superseded'
                                      ORDER BY id DESC LIMIT 200''',
                                   (item['conversation_id'], message_id)).fetchall()
            if not rows or rows[0]['id'] != message_id:
                raise LookupError('找不到这条消息')
            return {'message_id': message_id,
                    'messages': [self._message_view(row) for row in reversed(rows)]}

    def state(self, item_id='main', *, compact=False):
        with self.workspace() as ws, ws.conn:
            item = self.item(ws, item_id)
            cfg = self.config_for(item)
            rows = ws.conn.execute("SELECT * FROM messages WHERE conversation_id=? AND coalesce(kind,'')<>'superseded' ORDER BY id DESC LIMIT 200",
                                   (item['conversation_id'],)).fetchall()
            messages = [self._message_view(row, compact=compact) for row in reversed(rows)]
            run = ws.conn.execute('SELECT * FROM web_runs WHERE item_id=? ORDER BY rowid DESC LIMIT 1', (item_id,)).fetchone()
            selection = model_health.view(ws.conn, cfg,
                [cfg.model, *cfg.model_profiles.values()], last_run=run)
            context_notes = memory.notes(ws, item, active_only=True)
            notes = [dict(r) for r in ws.conn.execute("""SELECT * FROM assistant_memories WHERE user_id=?
                ORDER BY CASE priority WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 ELSE 2 END,updated_at DESC,id""", (self.user(),))]
            run_value = dict(run) if run else None
            if compact and run_value:
                # Completion payload can contain every stored evidence body; the
                # polling endpoint is the only caller that needs it.
                run_value.pop('result', None)
            from lodestar.web.interruptions import view as followup_view
            return {'followup':followup_view(ws,item_id),'item': item, 'items': self.task_list(ws),
                    'messages': messages, 'run': run_value, 'notes': notes,
                    'context_notes': context_notes,
                    'learning': learning.profile(ws.conn, user_id=self.user(item_id)),
                    'projects': repo.list_projects(ws.conn), 'configuration': snapshot(cfg),
                    'models': selection['models'], 'model_selection': selection,
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
        with self.workspace() as ws:
            run = ws.conn.execute('SELECT * FROM web_runs WHERE item_id=? ORDER BY rowid DESC LIMIT 1', (item_id,)).fetchone()
            selection = model_health.view(ws.conn, cfg, ids, last_run=run)
        return {**selection, 'reasoning': {model: reasoning_choices(
                    replace(cfg, model=model, llm_thinking=False, llm_thinking_mode='default'))
                    for model in selection['models']}}

    def restore_model(self, item_id, model):
        with self.lock, self.workspace() as ws, ws.conn:
            cfg = self.config_for(self.item(ws, item_id))
            model_health.restore(ws.conn, cfg, model)

    def select_model_default(self, item_id):
        """Only a fresh matching successful configuration, never a directory guess."""
        with self.lock, self.workspace() as ws:
            item = self.item(ws, item_id)
            cfg = self.config_for(item)
            run = ws.conn.execute('SELECT * FROM web_runs WHERE item_id=? ORDER BY rowid DESC LIMIT 1', (item_id,)).fetchone()
            selection = model_health.view(ws.conn, cfg, [cfg.model], last_run=run)
            target = selection['recommended_model']
            explicit = json.loads(item['settings']).get('model')
            if (not target or target == cfg.model or (explicit and not selection['current_blocked'])
                    or ws.conn.execute("SELECT 1 FROM web_runs WHERE item_id=? AND status IN ('queued','running','cancelling')", (item_id,)).fetchone()):
                return {'changed': False}
            self.update(item_id, {'settings': {'model': target}})
            return {'changed': True, 'model': target}

    def task_list(self, ws):
        rows = ws.conn.execute("SELECT i.*,r.status AS run_status,r.id AS run_id,(SELECT data FROM web_events WHERE run_id=r.id AND kind='progress' ORDER BY id DESC LIMIT 1) AS progress FROM web_items i LEFT JOIN web_runs r ON r.rowid=(SELECT MAX(rowid) FROM web_runs WHERE item_id=i.id) WHERE i.owner='local' ORDER BY i.created_at,i.id")
        return [dict(r) for r in rows]

    def project(self, data):
        return self.save_project({'name':data.get('name',''),'description':data.get('description',''),
                                 'allow_model_description':data.get('allow_model_description',False)})

    def library(self):
        with self.workspace() as ws:
            return {'projects':projects.catalog(ws),'items':self.task_list(ws)}

    def paper_library(self, data):
        from lodestar.memory import papers
        action = data.get('action', 'save')
        if action == 'refresh_impact':
            from lodestar.paper_impact import lookup
            with self.lock, self.workspace() as ws:
                if self.closing:
                    raise Conflict('服务正在关闭，请稍后再操作资料库')
                value = papers.detail(ws.conn, data.get('id'), data.get('project_id'))
            # External metadata requests must not hold the global application lock.
            with self.workspace() as ws, ws.conn:
                lookup(ws.conn, value['paper']['canonical_url'], title=value['paper']['title'], refresh=True)
                return papers.detail(ws.conn, data['id'], data.get('project_id'))
        with self.lock, self.workspace() as ws, ws.conn:
            if self.closing:
                raise Conflict('服务正在关闭，请稍后再操作资料库')
            if action == 'save':
                from lodestar.web import paper_hub
                existing_paper=ws.conn.execute('SELECT id FROM library_papers WHERE paper_key=?',(papers.identity(data.get('url',''))[0],)).fetchone()
                value=papers.save(ws.conn, data)
                paper_hub.ensure_state(ws.conn,value['paper']['id'])
                state=ws.conn.execute('SELECT revision FROM paper_study_state WHERE paper_id=?',(value['paper']['id'],)).fetchone()
                if data.get('study_revision') is not None or not existing_paper:
                    paper_hub.update(ws,{'id':value['paper']['id'],'revision':data.get('study_revision',state['revision']),
                        'status':data.get('status','inbox')})
                return paper_hub.detail(ws,value['paper']['id'],data.get('project_id'))
            if action == 'attach_inbox':
                return papers.attach_inbox(ws.conn, data.get('project_id'), data.get('recommendation_id'))
            raise ValueError('资料库操作无效')

    def save_project(self, data):
        with self.lock, self.workspace() as ws, ws.conn:
            if self.closing or f"project:{data.get('id')}" in self.project_operations:
                raise Conflict('项目正在同步或服务正在关闭，请完成后再修改')
            return projects.save(ws,data)

    def import_project(self, data):
        from lodestar.project_sources import repository_url
        project_id = data.get('id')
        operation = f'project:{project_id}' if project_id is not None else repository_url(data.get('url')).lower()
        with self.lock:
            if self.closing or operation in self.project_operations:
                raise Conflict('该项目已有导入或同步正在执行，请等待完成')
            self.project_operations[operation] = threading.current_thread()
            self.project_workers.add(threading.current_thread())
        try:
            return self._import_project(data,project_id)
        finally:
            with self.lock:
                for key,owner in list(self.project_operations.items()):
                    if owner is threading.current_thread():
                        del self.project_operations[key]
                self.project_workers.discard(threading.current_thread())

    def _import_project(self, data, project_id):
        # Network reads must not hold a DB transaction or block unrelated sends.
        checkout = None
        if project_id is not None:
            if type(project_id) is not int:
                raise ValueError('项目 ID 无效')
            with self.workspace() as ws:
                projects.check_revision(ws,project_id,data.get('revision'))
                source = ws.conn.execute('SELECT repository,branch FROM web_project_sources WHERE project_id=?',(project_id,)).fetchone()
                if not source:
                    raise ValueError('此项目还没有 GitHub 关联')
                url, branch = source['repository'], source['branch']
                checkout = ws.conn.execute('SELECT directory FROM web_project_checkouts WHERE project_id=?',(project_id,)).fetchone()
                if ws.conn.execute("SELECT 1 FROM web_runs r JOIN web_items i ON i.id=r.item_id WHERE i.project_id=? AND r.status IN ('queued','running','cancelling')",(project_id,)).fetchone():
                    raise Conflict('所属项目仍有任务运行，请等待或停止后再同步')
        else:
            url, branch = data.get('url'), data.get('branch','')
        snapshot = fetch_snapshot(url,branch)
        new = project_id is None
        if new:
            with self.lock, self.workspace() as ws, ws.conn:
                project_id = projects.apply_snapshot(ws,snapshot)
                self.project_operations[f'project:{project_id}'] = threading.current_thread()
            # Keep partial import visible if cloning fails; no rollback claim.
        try:
            if checkout:
                project_workspaces.sync(self.config.workspace_dir,checkout[0],snapshot)
                key = checkout[0]
            else:
                key = project_workspaces.clone(self.config.workspace_dir,snapshot)
        except RepositoryError as error:
            error.project_id = project_id
            raise
        with self.lock, self.workspace() as ws, ws.conn:
            if not new:
                projects.apply_snapshot(ws,snapshot,project_id=project_id,revision=data.get('revision'))
            ws.conn.execute('INSERT OR REPLACE INTO web_project_checkouts(project_id,directory) VALUES(?,?)',(project_id,key))
        return project_id

    def research_job(self,data):
        with self.lock:
            return self._research_job_locked(data)

    def _research_job_locked(self,data):
        from lodestar.web import research
        project_id,action=data.get('project_id'),data.get('action')
        if action not in ('read','assess','plan','tick'): raise ValueError('研究操作无效')
        key=data.get('key')
        if not isinstance(key,str) or not 1<=len(key)<=100: raise ValueError('请提供操作标识')
        goal=data.get('goal','')
        if not isinstance(goal,str) or len(goal)>4000:raise ValueError('研究目标过长')
        payload={'paper_id':data.get('paper_id'),'goal':goal}
        with self.lock, self.workspace() as ws, ws.conn:
            research.project(ws,project_id)
            previous=ws.conn.execute('SELECT id,payload,action FROM web_research_jobs WHERE project_id=? AND request_key=?',(project_id,key)).fetchone()
            encoded=json.dumps(payload,ensure_ascii=False,sort_keys=True)
            if previous:
                if previous['payload']!=encoded or previous['action']!=action:raise Conflict('操作标识已用于其他请求')
                return previous['id']
            if self.closing or f'project:{project_id}' in self.project_operations:raise Conflict('项目操作中，请等待结束')
            if ws.conn.execute("SELECT 1 FROM web_runs r JOIN web_items i ON i.id=r.item_id WHERE i.project_id=? AND r.status IN ('queued','running','cancelling')",(project_id,)).fetchone():raise Conflict('项目任务仍在运行')
            if action!='tick':
                from lodestar.agent.candidate import _recommendation
                _recommendation(ws,project_id,payload['paper_id'])
            cfg=self.config_for(self.item(ws,'main'))
            if action in ('assess','plan'):
                if cfg.model_calls_disabled and cfg.llm_mode!='mock':raise Conflict('模型调用已暂停；未发请求')
                if model_health.view(ws.conn,cfg,[cfg.model])['current_blocked']:raise Conflict('当前模型不可用；未自动切换')
            job_id=uuid.uuid4().hex
            ws.conn.execute('INSERT INTO web_research_jobs(id,project_id,action,status,request_key,payload,config) VALUES(?,?,?,?,?,?,?)',
                (job_id,project_id,action,'queued',key,encoded,json.dumps(snapshot(cfg))))
        worker=None
        try:
            worker=threading.Thread(target=self.execute_research,args=(job_id,project_id,action,payload,cfg),daemon=True)
            self.project_operations[f'project:{project_id}']=worker;self.project_workers.add(worker)
            worker.start()
        except Exception:
            with self.lock,self.workspace() as ws,ws.conn:
                self.project_workers.discard(worker);self.project_operations.pop(f'project:{project_id}',None)
                ws.conn.execute("UPDATE web_research_jobs SET status='error',result=? WHERE id=?",(json.dumps({'error':'工作线程未能启动，未自动重试'}),job_id))
                from lodestar.web.background import notify
                notify(ws.conn,'research:'+job_id,'research',project_id,'error')
            raise Conflict('研究操作未能启动；已保留失败记录') from None
        return job_id

    def execute_research(self,job_id,project_id,action,payload,cfg):
        from lodestar.agent import candidate,watch
        llm=None
        try:
            with self.workspace(cfg) as ws:
                with ws.conn:ws.conn.execute("UPDATE web_research_jobs SET status='running' WHERE id=?",(job_id,))
                if action=='read':result=candidate.read(ws,project_id,payload['paper_id'])
                elif action=='tick':
                    result=watch.tick(ws,project_id=project_id)
                    if any(r['status']!='ok' for r in result['runs']): result['status']='error'
                else:
                    from lodestar.agent.project_evidence import collect
                    saved=candidate.evidence(ws,project_id,payload['paper_id'])
                    context=collect(ws,payload['goal'],[saved['evidence']] if saved else [],project_id,live=True)
                    if context['status']!='ready' or not context['documents'] or not context['papers']:
                        result={'status':'insufficient_evidence','missing':context['missing'],'model_calls':0}
                    else:
                        llm=self.llm_factory(cfg)
                        result=(candidate.assess(ws,llm,project_id,payload['paper_id'],payload['goal']) if action=='assess' else
                                candidate.plan(ws,llm,project_id,payload['paper_id'],payload['goal']))
                with ws.conn:ws.conn.execute('UPDATE web_research_jobs SET status=?,result=? WHERE id=?',
                    ('completed' if (action=='tick' and result.get('status')!='error') or (action=='read' and result.get('status')=='read') or (action=='assess' and result.get('status')=='assessed') or (action=='plan' and result.get('status')=='draft' and result.get('contract_valid')) else 'error',json.dumps(result,ensure_ascii=False),job_id))
        except Exception:
            with self.workspace() as ws,ws.conn:ws.conn.execute("UPDATE web_research_jobs SET status='error',result=? WHERE id=?",(json.dumps({'error':'研究操作未完成；已保存的证据保留，未自动重试'}),job_id))
        finally:
            try:
                from lodestar.web.background import notify
                with self.workspace() as ws,ws.conn:
                    row=ws.conn.execute('SELECT status,result FROM web_research_jobs WHERE id=?',(job_id,)).fetchone()
                    result=json.loads(row['result']) if row else {}
                    # Unchanged successful subscription checks stay quiet.
                    meaningful=action!='tick' or not row or row['status']!='completed' or any(r.get('new_recommendations',0)>0 for r in result.get('runs',[]))
                    if row and meaningful:notify(ws.conn,'research:'+job_id,'research',project_id,row['status'])
            finally:
                try:
                    if llm is not None:llm.close()
                finally:
                    with self.lock:
                        self.project_operations.pop(f'project:{project_id}',None);self.project_workers.discard(threading.current_thread())

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

    def submit(self, item_id, message, key, *, reference_text='', background=False, policy=None):
        # Shutdown must not release the workspace while a committed queued run
        # is still between persistence, worker startup and startup-error cleanup.
        with self.lock:
            if self.closing:
                raise Conflict('服务正在关闭；请重启后再发送')
            return self._submit_locked(item_id, message, key, reference_text=reference_text,
                                       background=background, policy=policy)

    def _submit_locked(self, item_id, message, key, *, reference_text, background, policy,
                       retry_user_message_id=None, continue_message_id=None, internal_followup=False):
        if not isinstance(message, str) or not message.strip() or len(message) > 24000:
            raise ValueError('请输入 1–24000 字的问题')
        if not isinstance(reference_text, str):
            raise ValueError('参考文本必须是文字')
        reference_text = reference_text.strip()
        if len(message) + len(reference_text) > 24000:
            raise ValueError('问题与参考文本合计不能超过 24000 字')
        if not isinstance(key, str) or not 1 <= len(key) <= 100:
            raise ValueError('提交标识无效')
        if retry_user_message_id is not None and (type(retry_user_message_id) is not int or retry_user_message_id < 1):
            raise ValueError('重试消息无效')
        with self.lock, self.workspace() as ws, ws.conn:
            item = self.item(ws, item_id)
            existing = ws.conn.execute('SELECT id FROM web_runs WHERE item_id=? AND request_key=?', (item_id,key)).fetchone()
            if existing:
                return existing['id']
            if not internal_followup and ws.conn.execute("SELECT 1 FROM web_followups WHERE item_id=? AND status IN ('pending','starting')",(item_id,)).fetchone():raise Conflict('补充已收到，正在接续')
            if item['status'] != 'active':
                raise Conflict('请先恢复此任务')
            if ws.conn.execute("SELECT 1 FROM web_runs WHERE item_id=? AND status IN ('queued','running','cancelling')", (item_id,)).fetchone():
                raise Conflict('当前空间正在回答，请稍后再发送')
            cfg = self.config_for(item)
            if f"project:{item['project_id']}" in self.project_operations:
                raise Conflict('所属项目正在同步工作区，请完成后再发送')
            selection = model_health.view(ws.conn, cfg, [cfg.model])
            if selection['current_blocked']:
                raise Conflict('此模型或请求配置已确认不可用，请先在模型设置中选择其他模型；不会自动重试或付费回退')
            if cfg.model_calls_disabled and cfg.llm_mode != 'mock':
                raise Conflict('模型调用已暂停，请在模型设置中启用后发送')
            ws.conn.execute("UPDATE web_followups SET status='dismissed' WHERE item_id=? AND status IN ('error','interrupted')",(item_id,))
            run_id = uuid.uuid4().hex
            ws.conn.execute('INSERT INTO web_runs(id,item_id,request_key,status,message,config,policy,retry_user_message_id,continue_message_id) VALUES(?,?,?,?,?,?,?,?,?)',
                            (run_id,item_id,key,'queued',message,json.dumps(snapshot(cfg)),json.dumps(policy or {},ensure_ascii=False),retry_user_message_id,continue_message_id))
            if retry_user_message_id is not None:
                # Keep source IDs and committed action receipts, retire the old prose.
                old_replies = ws.conn.execute("SELECT id,metadata FROM messages WHERE conversation_id=? AND id>? AND role='assistant' AND coalesce(kind,'')<>'superseded'",
                    (item['conversation_id'], retry_user_message_id)).fetchall()
                for old in old_replies:
                    previous = json.loads(old['metadata'] or '{}')
                    retired = {'replaced_by_run': run_id, 'source_removed': True, 'run_id': previous.get('run_id'),
                               'run_ids': previous.get('run_ids', []),
                               'action_receipts': previous.get('action_receipts', [])}
                    ws.conn.execute("UPDATE messages SET content='',kind='superseded',metadata=? WHERE id=?",
                                    (json.dumps(retired,ensure_ascii=False),old['id']))
            signal = self.cancel_signals[run_id] = threading.Event()
        try:
            def work(*worker_args):
                try:
                    self.execute(*worker_args)
                finally:
                    with self.lock:
                        self.workers.pop(run_id, None)
                    from lodestar.web.interruptions import dispatch
                    dispatch(self,item['id'])

            with self.lock:
                if self.closing:
                    raise Conflict('服务正在关闭；本轮未启动')
                worker = threading.Thread(target=work, args=(run_id,item,cfg,message,reference_text,signal,background,retry_user_message_id), daemon=True)
                self.workers[run_id] = worker
                worker.start()
        except Exception:
            # No worker exists to release this queued slot. Keep the failed
            # attempt visible, then propagate the actual startup exception.
            try:
                with self.lock, self.workspace() as ws, ws.conn:
                    # A stop acknowledged during startup stays a user cancellation.
                    status = 'cancelled' if signal.is_set() else 'error'
                    answer = ('本轮已停止，运行记录已保留。' if signal.is_set() else
                              '本轮未启动，记录已保留。可以重试；不会自动调用模型。')
                    failure = {'answer': answer, 'runtime_error': {'code': 'worker_start_failed'}, 'action_receipts': []}
                    updated = ws.conn.execute(
                        "UPDATE web_runs SET status=?,result=? WHERE id=? AND status IN ('queued','cancelling')",
                        (status, json.dumps(failure, ensure_ascii=False), run_id))
                    if updated.rowcount:
                        if continue_message_id is not None:
                            # A failed worker start must never replace the existing prose.
                            return run_id
                        if retry_user_message_id is None:
                            repo.add_message(ws.conn, item['conversation_id'], 'user', message,
                                             metadata={'run_id': run_id,
                                                       **({'reference_text': reference_text} if reference_text else {})})
                        repo.add_message(ws.conn, item['conversation_id'], 'assistant', answer, kind=status,
                                         metadata={'run_id': run_id, 'status': status, **failure})
            finally:
                with self.lock:
                    self.cancel_signals.pop(run_id, None)
                    self.workers.pop(run_id, None)
            raise
        return run_id

    def retry(self, item_id, message_id, key):
        """Regenerate only the current answer, reusing its original user turn.

        This transcript is linear rather than branch-based. Retrying an earlier
        reply after later messages would append an answer out of context, so the
        endpoint deliberately accepts only the latest assistant message.
        """
        if type(message_id) is not int or message_id < 1:
            raise ValueError('消息标识无效')
        with self.lock, self.workspace() as ws:
            item = self.item(ws, item_id)
            existing = ws.conn.execute('SELECT id FROM web_runs WHERE item_id=? AND request_key=?', (item_id,key)).fetchone()
            if existing:
                return existing['id']
            target = ws.conn.execute('''SELECT * FROM messages WHERE id=? AND conversation_id=? AND role='assistant' ''',
                                     (message_id, item['conversation_id'])).fetchone()
            latest = ws.conn.execute('''SELECT id FROM messages WHERE conversation_id=? AND coalesce(kind,'')<>'superseded'
                                        ORDER BY id DESC LIMIT 1''', (item['conversation_id'],)).fetchone()
            if target is None or latest is None or latest['id'] != message_id:
                raise Conflict('只能重新生成当前最后一条回答')
            user = ws.conn.execute('''SELECT * FROM messages WHERE conversation_id=? AND role='user' AND id<?
                                      ORDER BY id DESC LIMIT 1''',
                                   (item['conversation_id'], message_id)).fetchone()
            if user is None:
                raise LookupError('找不到这条回答对应的问题')
            try:
                metadata = json.loads(user['metadata'] or '{}')
            except ValueError:
                metadata = {}
            reference_text = metadata.get('reference_text', '') if isinstance(metadata, dict) else ''
            if not isinstance(reference_text, str):
                reference_text = ''
            message = user['content']
            return self._submit_locked(item_id, message, key, reference_text=reference_text,
                                       background=False, policy={}, retry_user_message_id=user['id'])

    def continue_answer(self, item_id, message_id, key):
        from lodestar.agent.continuation import can_continue
        if type(message_id) is not int or message_id < 1:
            raise ValueError('消息标识无效')
        with self.lock, self.workspace() as ws:
            item = self.item(ws, item_id)
            existing = ws.conn.execute('SELECT id FROM web_runs WHERE item_id=? AND request_key=?', (item_id,key)).fetchone()
            if existing:
                return existing['id']
            target = ws.conn.execute('SELECT * FROM messages WHERE conversation_id=? AND id=? AND role=\'assistant\'',
                                     (item['conversation_id'],message_id)).fetchone()
            latest = ws.conn.execute("SELECT id FROM messages WHERE conversation_id=? AND coalesce(kind,'')<>'superseded' ORDER BY id DESC LIMIT 1",
                                     (item['conversation_id'],)).fetchone()
            if not target or not latest or latest['id'] != message_id or not can_continue(dict(target)):
                raise Conflict('只能继续最后一条已有正文、尚未完成的回答')
            # Reserve the run under the same lock as eligibility validation.
            return self._submit_locked(item_id, '继续完成上一条回答', key, reference_text='',
                background=False, policy={}, continue_message_id=message_id)

    def delete_message(self, item_id, message_id):
        """Tombstone IDs for source integrity; remove prose and replayable run data."""
        if type(message_id) is not int or message_id < 1:
            raise ValueError('消息标识无效')
        with self.lock, self.workspace() as ws, ws.conn:
            item = self.item(ws, item_id)
            if ws.conn.execute("SELECT 1 FROM web_runs WHERE item_id=? AND status IN ('queued','running','cancelling')", (item_id,)).fetchone():
                raise Conflict('先等 lolo 停下，再删除这条消息吧')
            target = ws.conn.execute('SELECT * FROM messages WHERE id=? AND conversation_id=?',
                                     (message_id,item['conversation_id'])).fetchone()
            if target is None:
                raise LookupError('找不到这条消息')
            rows = [target]
            if target['role'] == 'user':
                following = ws.conn.execute('SELECT * FROM messages WHERE conversation_id=? AND id>? ORDER BY id',
                                             (item['conversation_id'],message_id)).fetchall()
                for row in following:
                    if row['role'] == 'user':
                        break
                    rows.append(row)
            ids = [row['id'] for row in rows]
            run_ids = set()
            for row in rows:
                previous = json.loads(row['metadata'] or '{}')
                run_ids.update(previous.get('run_ids', []))
                if previous.get('run_id'):
                    run_ids.add(previous['run_id'])
                # Older retries / continuations may also contain the removed text.
                for run in ws.conn.execute('SELECT id,result FROM web_runs WHERE item_id=? AND (retry_user_message_id=? OR continue_message_id=?)',
                                            (item_id,row['id'],row['id'])):
                    run_ids.add(run['id'])
                retired = {'source_removed': True, 'deleted_at': repo._now(),
                           'action_receipts': previous.get('action_receipts', [])}
                ws.conn.execute("UPDATE messages SET content='',kind='superseded',metadata=? WHERE id=?",
                    (json.dumps(retired,ensure_ascii=False),row['id']))
            for run_id in run_ids:
                if not ws.conn.execute('SELECT 1 FROM web_runs WHERE id=? AND item_id=?', (run_id,item_id)).fetchone():
                    continue
                receipts = self.action_receipts(ws, run_id)
                ws.conn.execute("UPDATE web_runs SET message='',result=? WHERE id=? AND item_id=?",
                    (json.dumps({'source_removed':True,'action_receipts':receipts},ensure_ascii=False),run_id,item_id))
                ws.conn.execute("DELETE FROM web_events WHERE run_id=? AND kind<>'action'", (run_id,))
            # Reconstruct future source focus from remaining visible messages.
            ws.conn.execute("UPDATE agent_sessions SET evidence='[]' WHERE conversation_id=?", (item['conversation_id'],))
            return {'deleted_ids': ids}

    def start_scheduler(self):
        from lodestar.web.background import Scheduler
        with self.lock:
            if self.scheduler is None and not self.closing:
                scheduler=Scheduler(self)
                scheduler.start()
                self.scheduler=scheduler

    def close(self):
        """Keep the process lease until all cooperative writes/clients have stopped."""
        with self.lock:
            self.closing = True
            if self.scheduler is not None:self.scheduler.stop.set()
            for signal in self.cancel_signals.values():
                signal.set()
            workers = list(self.workers.values()) + list(self.project_workers)
        if self.scheduler is not None:self.scheduler.thread.join()
        for worker in workers:
            if worker is not threading.current_thread():
                worker.join()

    def cancel(self, item_id, run_id):
        if not isinstance(run_id, str) or not run_id:
            raise ValueError('运行标识无效')
        with self.lock, self.workspace() as ws, ws.conn:
            self.item(ws, item_id)
            ws.conn.execute("UPDATE web_followups SET status='cancelled' WHERE item_id=? AND status='pending'",(item_id,))
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

    def execute(self, run_id, item, cfg, message, reference_text, signal, background=False,
                retry_user_message_id=None):
        started_at = time.time()
        clock_start = time.monotonic()
        timing = {'first_text_ms': None, 'phases': []}
        def check_cancel():
            if signal.is_set():
                raise LLMError('Run cancelled by user', code='cancelled', role='conversation')

        with self.workspace(cfg) as ws:
            llm = None
            try:
                with self.lock, ws.conn:
                    row = ws.conn.execute('SELECT status,policy,retry_user_message_id,continue_message_id FROM web_runs WHERE id=?', (run_id,)).fetchone()
                    if row['status'] not in ('queued','cancelling'):
                        return
                    ws.conn.execute("UPDATE web_runs SET status='running' WHERE id=? AND status='queued'", (run_id,))
                policy = json.loads(row['policy'])
                retry_user_message_id = row['retry_user_message_id']
                continue_message_id = row['continue_message_id']
                # A queued cancellation still records its input and stopped answer,
                # but never constructs a provider client or starts inference.
                llm = None if signal.is_set() else self.llm_factory(cfg)
                if isinstance(llm, LLMClient):
                    llm.cancel_signal = signal
                from lodestar.memory.assistant import mark_accessed, prompt_rows, reconstruct_context, select_rows
                from lodestar.web.tools import assistant_tools
                with ws.conn:
                    recall_query = message + '\n' + reference_text
                    note_rows = select_rows(memory.notes(ws, item, active_only=True), recall_query)
                    mark_accessed(ws.conn, note_rows)
                    note_evidence = reconstruct_context(ws.conn, note_rows, recall_query, item_id=item['id'])
                notes = prompt_rows(note_rows, note_evidence)
                def deliver(text):
                    check_cancel()
                    if text and timing['first_text_ms'] is None:
                        timing['first_text_ms'] = round((time.monotonic()-clock_start)*1000)
                    self.event(ws,run_id,'text',text)

                def progress(action):
                    check_cancel()
                    if len(timing['phases']) < 64:
                        timing['phases'].append({'action':action,'elapsed_ms':round((time.monotonic()-clock_start)*1000)})
                    self.event(ws,run_id,'progress',action)
                agent = self.agent_factory(ws,llm)
                if continue_message_id is not None:
                    from lodestar.agent.continuation import continue_turn
                    result = continue_turn(agent,item['conversation_id'],continue_message_id,
                        user_id=self.user(item['id']),on_text=deliver,on_progress=progress,check_cancel=check_cancel,run_id=run_id)
                else:
                    result = agent.turn(item['conversation_id'],message,
                    user_id=self.user(item['id']), memory_notes=notes, check_cancel=check_cancel,
                    reference_text=reference_text,
                    history_after=item['context_after'], track_exposure=False,
                    execution_policy=policy,
                    reuse_user_message_id=retry_user_message_id,
                    assistant_tools=assistant_tools(self, item, run_id, message, check_cancel, user_authored=not background, execution_policy=policy),
                    on_text=deliver, on_progress=progress)
                timing['response_ms'] = round((time.monotonic()-clock_start)*1000)
                result['timing'] = timing
                scope = {'item_id': item['id'], 'project_id': item['project_id'], 'user_id': self.user(),
                         'history_after': item['context_after'], 'background': background, 'execution_policy': policy,
                         'retry_user_message_id': retry_user_message_id,
                         'memory': [{'id': r['id'], 'scope': r['scope'], 'revision': r['revision']} for r in note_rows]}
                result['web_context'] = scope
                result['action_receipts'] = self.action_receipts(ws, run_id)
                with self.lock, ws.conn:
                    model_health.observe(ws.conn, cfg, result, llm, started_at)
                last = ws.conn.execute("SELECT id,metadata FROM messages WHERE conversation_id=? AND role='assistant' AND coalesce(kind,'')<>'superseded' ORDER BY id DESC LIMIT 1", (item['conversation_id'],)).fetchone()
                # Capture the evidence before releasing the foreground slot. A
                # subsequent turn can replace session evidence while this worker
                # records learning in the background.
                exposure_sources = []
                if (continue_message_id is None and not background and result.get('status') not in ('error', 'cancelled')
                        and result.get('route_reason') != 'resolved paper link'):
                    session = ws.conn.execute('SELECT evidence FROM agent_sessions WHERE conversation_id=?',
                                              (item['conversation_id'],)).fetchone()
                    exposure_sources = [s for s in json.loads(session['evidence'])
                                        if s.get('source_type') == 'paper' and s.get('content')] if session else []
                if exposure_sources:
                    result['learning_exposure_status'] = 'pending'
                if last:
                    previous = json.loads(last['metadata'])
                    if continue_message_id is not None:
                        result['action_receipts'] = previous.get('action_receipts', []) + result['action_receipts']
                    run_ids = list(dict.fromkeys([*previous.get('run_ids', []), previous.get('run_id'), run_id]))
                    metadata = {**previous, 'web_context': scope, 'run_id': run_id, 'run_ids': [r for r in run_ids if r],
                                'action_receipts': result['action_receipts'], 'timing':timing,
                                **({'learning_exposure_status': 'pending'} if exposure_sources else {})}
                    with ws.conn:
                        ws.conn.execute('UPDATE messages SET metadata=? WHERE id=?', (json.dumps(metadata,ensure_ascii=False),last['id']))
                with self.lock, ws.conn:
                    status = 'error' if result.get('status') == 'error' else 'completed'
                    if signal.is_set() or result.get('status') == 'cancelled':
                        status = 'cancelled'
                    ws.conn.execute("UPDATE web_runs SET status=?, result=? WHERE id=? AND status IN ('running','cancelling')",
                        (status,json.dumps(result,ensure_ascii=False),run_id))
                # The reply is now complete and the next user turn is allowed.
                # Keep ownership of this worker/client until the optional audit
                # finishes, without keeping the conversation in 'running'.
                if status == 'completed' and exposure_sources and last:
                    self._record_reply_exposure(ws, llm, item, run_id, last['id'],
                                                message, result['answer'], exposure_sources, signal)
            except Exception:
                # Provider bodies and credentials must never become browser diagnostics.
                with self.lock, ws.conn:
                    status = 'cancelled' if signal.is_set() else 'error'
                    answer = '本轮已停止，运行记录已保留。' if signal.is_set() else '本轮未完成，记录已保留。请检查本地服务配置后重试。'
                    receipts = self.action_receipts(ws, run_id)
                    last = ws.conn.execute("SELECT role FROM messages WHERE conversation_id=? AND coalesce(kind,'')<>'superseded' ORDER BY id DESC LIMIT 1",
                                           (item['conversation_id'],)).fetchone()
                    if last and (last['role'] == 'user' or retry_user_message_id is not None):
                        repo.add_message(ws.conn, item['conversation_id'], 'assistant', answer,
                                         metadata={'run_id': run_id, 'status': status, 'action_receipts': receipts})
                    ws.conn.execute("UPDATE web_runs SET status=?,result=? WHERE id=? AND status IN ('queued','running','cancelling')",
                        (status,json.dumps({'answer':answer,'action_receipts':receipts},ensure_ascii=False),run_id))
            finally:
                try:
                    from lodestar.web.background import notify
                    with ws.conn:
                        terminal=ws.conn.execute('SELECT status FROM web_runs WHERE id=?',(run_id,)).fetchone()
                        if terminal and terminal[0] not in ('queued','running','cancelling'):
                            notify(ws.conn,'run:'+run_id,'task',item['id'],terminal[0])
                finally:
                    with self.lock:
                        self.cancel_signals.pop(run_id, None)
                    if llm is not None:
                        llm.close()

    def _record_reply_exposure(self, ws, llm, item, run_id, message_id, goal, answer, sources, signal):
        """Optional post-answer work cannot fail or overwrite the foreground reply."""
        from lodestar.agent.exposure import record_exposure
        try:
            if self.closing or signal.is_set():
                outcome = {'learning_exposure_status': 'cancelled'}
            else:
                task = ws.conn.execute('SELECT task_id FROM agent_sessions WHERE conversation_id=?',
                                       (item['conversation_id'],)).fetchone()
                exposure = record_exposure(ws, llm, task['task_id'] if task else None,
                    self.user(item['id']), answer, sources, goal=goal)
                outcome = {'learning_exposure': exposure,
                           'learning_exposure_status': 'recorded' if exposure else 'no_supported_methods'}
        except Exception as error:
            outcome = {'learning_exposure_status': 'error', 'learning_exposure_error': type(error).__name__}
        try:
            with self.lock, ws.conn:
                row = ws.conn.execute('SELECT metadata,kind FROM messages WHERE id=?', (message_id,)).fetchone()
                if row and row['kind'] != 'superseded':
                    metadata = {**json.loads(row['metadata']), **outcome}
                    ws.conn.execute('UPDATE messages SET metadata=? WHERE id=?',
                                    (json.dumps(metadata, ensure_ascii=False), message_id))
                run = ws.conn.execute('SELECT result FROM web_runs WHERE id=?', (run_id,)).fetchone()
                if run and not json.loads(run['result']).get('source_removed'):
                    ws.conn.execute('UPDATE web_runs SET result=? WHERE id=?',
                        (json.dumps({**json.loads(run['result']), **outcome}, ensure_ascii=False), run_id))
        except Exception:
            # The already-committed answer remains complete if optional audit
            # persistence fails (e.g. shutdown or a transient local DB error).
            pass

    def events(self, run_id, after):
        with self.workspace() as ws:
            row = ws.conn.execute('SELECT r.* FROM web_runs r JOIN web_items i ON i.id=r.item_id WHERE r.id=? AND i.owner=?', (run_id,'local')).fetchone()
            if row is None:
                raise LookupError('找不到这次运行')
            events = [{'id':r['id'],'kind':r['kind'],'data':json.loads(r['data'])} for r in ws.conn.execute('SELECT * FROM web_events WHERE run_id=? AND id>? ORDER BY id LIMIT 500', (run_id,after))]
            return {'run':dict(row),'events':events}


def make_server(app, port=8123):
    class Handler(BaseHTTPRequestHandler):
        timeout = 5  # Bound partial headers/bodies, including rejected local clients.

        def log_message(self, *_):
            pass

        def deny(self, message):
            # Closing with an unread POST body can reset the TCP connection
            # before Windows clients observe the 403. Drain only a bounded,
            # declared body; never parse it or call application/model tools.
            self.close_connection = True
            if self.command == 'POST' and not self.headers.get('Transfer-Encoding'):
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if 0 < length <= 100000:
                        self.rfile.read(length)
                except (OSError, ValueError):
                    pass
            return self.respond({'error': message}, 403)

        def respond(self, data, status=200, mime='application/json; charset=utf-8'):
            body = json.dumps(data,ensure_ascii=False).encode() if mime.startswith('application/json') else data
            self.send_response(status)
            self.send_header('Content-Type',mime)
            self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Referrer-Policy','same-origin')
            policy = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src data:; connect-src 'none'; base-uri 'none'; frame-ancestors 'self'"
                      if urlparse(self.path).path == '/diagram.html' else
                      "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
            self.send_header('Content-Security-Policy', policy)
            self.end_headers()
            self.wfile.write(body)

        def dispatch(self, write=False):
            app = self.server.app
            expected = {f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}
            if self.headers.get('Host') not in expected:
                return self.deny('仅支持本机访问')
            if write and (self.headers.get('X-Lodestar-Token') != app.token or self.headers.get('Origin') not in (None,*('http://'+h for h in expected))):
                return self.deny('请从本地工作空间提交')
            parsed = urlparse(self.path)
            path, query = parsed.path, parse_qs(parsed.query)
            try:
                if not write:
                    if path in ('/','/memory','/papers','/projects','/research','/history'):
                        template = 'index.html' if path=='/' else 'history.html' if path=='/history' else 'workbench.html' if path=='/research' else 'library.html'
                        page = (STATIC/template).read_text(encoding='utf-8').replace('__TOKEN__',app.token)
                        return self.respond(page.encode(),mime='text/html; charset=utf-8')
                    if path == '/diagram.html':
                        return self.respond((STATIC/'diagram.html').read_bytes(),mime='text/html; charset=utf-8')
                    if path in ('/lolo-avatar.png','/lolo-companion.png','/lolo-pajamas.png'):
                        return self.respond((STATIC/path[1:]).read_bytes(),mime='image/png')
                    if path == '/companion.mjs':
                        return self.respond((STATIC/'companion.mjs').read_bytes(),mime='text/javascript; charset=utf-8')
                    if path == '/impact.mjs':
                        return self.respond((STATIC/'impact.mjs').read_bytes(),mime='text/javascript; charset=utf-8')
                    if path == '/response-ui.mjs':
                        return self.respond((STATIC/'response-ui.mjs').read_bytes(),mime='text/javascript; charset=utf-8')
                    if path in ('/mermaid.bundle.js','/diagram-frame.js','/diagram-frame.css','/diagrams.mjs','/mermaid-notices.txt','/mermaid.bundle.js.LEGAL.txt'):
                        mime = 'text/css' if path.endswith('.css') else 'text/plain' if path.endswith('.txt') else 'text/javascript'
                        return self.respond((STATIC/path[1:]).read_bytes(),mime=mime+'; charset=utf-8')
                    if path in ('/app.mjs','/history.mjs','/paper-study.mjs','/navigation.mjs','/markdown.mjs','/sources.mjs','/insights.mjs','/actions.mjs','/quote.mjs','/timeline.mjs','/followups.mjs','/stream.mjs','/models.mjs','/library.mjs','/library-views.mjs','/workbench.mjs','/library.css','/style.css','/icon.svg'):
                        mime = 'text/css' if path.endswith('.css') else 'image/svg+xml' if path.endswith('.svg') else 'text/javascript'
                        return self.respond((STATIC/path[1:]).read_bytes(),mime=mime+'; charset=utf-8')
                    if path == '/api/service':
                        return self.respond({'instance_id':app.instance_id,'pid':os.getpid()})
                    if path == '/api/companion/status':
                        from lodestar.desktop import status
                        with app.workspace() as ws:return self.respond(status(ws.conn))
                    if path == '/api/background':
                        from lodestar.web.background import view
                        with app.workspace() as ws:return self.respond(view(ws))
                    if path == '/api/state':
                        return self.respond(app.state(query.get('item',['main'])[0], compact=True))
                    if path == '/api/message-inspection':
                        return self.respond(app.message_inspection(query.get('item',['main'])[0],
                                                                     int(query.get('message',['0'])[0])))
                    if path in ('/api/conversation-search','/api/conversation-context'):
                        from lodestar.web import conversation_search
                        with app.workspace() as ws,ws.conn:
                            if path.endswith('context'):
                                return self.respond(conversation_search.context(ws,query.get('item',['main'])[0],int(query.get('message',['0'])[0])))
                            return self.respond(conversation_search.search(ws,query=query.get('query',[''])[0],project_id=query.get('project',[None])[0],
                                item_id=query.get('item',[''])[0],role=query.get('role',[''])[0],days=query.get('days',[''])[0],page=int(query.get('page',['1'])[0])))
                    if path == '/api/papers':
                        from lodestar.web import paper_hub
                        with app.lock,app.workspace() as ws,ws.conn:
                            paper_hub.sync(ws)
                            if query.get('id'):
                                return self.respond(paper_hub.detail(ws,int(query['id'][0]),int(query['project'][0]) if query.get('project') else None))
                            return self.respond(paper_hub.catalog(ws,query=query.get('query',[''])[0],status=query.get('status',[''])[0],mastery=query.get('mastery',[''])[0],
                                project_id=query.get('project',[None])[0],page=int(query.get('page',['1'])[0])))
                    if path in ('/api/history','/api/history-message'):
                        from lodestar.web import history
                        with app.workspace() as ws,ws.conn:
                            ws.conn.execute('BEGIN')
                            if path.endswith('message'):return self.respond(history.message(ws,query.get('item',['main'])[0],int(query.get('message',['0'])[0])))
                            return self.respond(history.search(ws,query.get('query',[''])[0],project_id=int(query['project'][0]) if query.get('project') else None,
                                item_id=query.get('item',[None])[0],page=int(query.get('page',['1'])[0])))
                    if path in ('/api/research','/api/research-paper'):
                        from lodestar.web import research
                        with app.workspace() as ws,ws.conn:
                            project_id=int(query.get('project',['0'])[0]);research.project(ws,project_id)
                            return self.respond(research.detail(ws,project_id,int(query.get('paper',['0'])[0])) if path.endswith('paper') else
                                research.catalog(ws,project_id,query.get('state',[''])[0],int(query.get('page',['1'])[0])))
                    if path in ('/api/vault-notes','/api/vault-note'):
                        from lodestar.web import vaults
                        with app.lock,app.workspace() as ws:
                            project_id=int(query.get('id',['0'])[0]);projects.detail(ws,project_id)
                            return self.respond(vaults.catalog(ws,project_id,query.get('q',[''])[0]) if path.endswith('notes') else vaults.read(ws,project_id,query.get('path',[''])[0]))
                    if path == '/api/library':
                        return self.respond(app.library())
                    if path == '/api/paper-library':
                        from lodestar.memory import papers
                        with app.workspace() as ws:
                            if query.get('id'):
                                return self.respond(papers.detail(ws.conn, int(query['id'][0]),
                                                                  int(query['project'][0]) if query.get('project') else None))
                            return self.respond(papers.catalog(ws.conn, query=query.get('query',[''])[0],
                                                              project_id=query.get('project',[None])[0],
                                                              status=query.get('status',[''])[0],
                                                              priority=query.get('priority',[''])[0],
                                                              page=int(query.get('page',['1'])[0])))
                    if path == '/api/project':
                        with app.workspace() as ws:
                            return self.respond(projects.detail(ws,int(query.get('id',['0'])[0])))
                    if path in ('/api/project-files','/api/project-file-preview'):
                        from lodestar.web import project_files
                        with app.lock, app.workspace() as ws:
                            project_id=int(query.get('id',['0'])[0])
                            if not repo.get_project(ws.conn,project_id): raise LookupError('找不到项目')
                            return self.respond(project_files.catalog(ws,project_id) if path.endswith('files') else
                                project_files.preview(ws,project_id,query.get('path',[''])[0]))
                    if path == '/api/memory-library':
                        with app.workspace() as ws, ws.conn:
                            return self.respond(memory.catalog(ws,query=query.get('query',[''])[0],
                                scope=query.get('scope',[''])[0],status=query.get('status',[''])[0],
                                project_id=int(query['project'][0]) if query.get('project') else None,
                                page=int(query.get('page',['1'])[0])))
                    if path == '/api/memory-history':
                        with app.workspace() as ws:
                            return self.respond(memory.history(ws,query.get('id',[''])[0]))
                    if path == '/api/learning-library':
                        text = query.get('query',[''])[0]
                        if len(text)>200:
                            raise ValueError('查询过长')
                        with app.workspace() as ws, ws.conn:
                            return self.respond(learning.retrieve(ws.conn,user_id=memory.USER,query=text or None,limit=20))
                    if path == '/api/tasks':
                        with app.workspace() as ws:
                            return self.respond({'items': app.task_list(ws)})
                    if path == '/api/events':
                        return self.respond(app.events(query.get('run',[''])[0],int(query.get('after',['0'])[0])))
                    return self.respond({'error':'页面不存在'},404)
                length = int(self.headers.get('Content-Length','0'))
                if not 0 < length <= (220000 if path=='/api/vault-note' else 100000):
                    return self.respond({'error':'请求大小无效'},413)
                data = json.loads(self.rfile.read(length))
                if not isinstance(data,dict):
                    raise ValueError('请求格式无效')
                if path == '/api/companion/desktop':
                    from lodestar.desktop import launch
                    action = data.pop('action', 'show')
                    if action not in ('show','stop'):
                        raise ValueError('无效的桌面操作')
                    return self.respond(launch(app.config.workspace_dir,port=self.server.server_port,
                        settings=data or None,stop=action=='stop'))
                if path == '/api/service-stop':
                    if data.get('instance_id')!=app.instance_id:raise ValueError('实例标识不匹配')
                    self.respond({'stopping':True})
                    threading.Thread(target=self.server.shutdown,daemon=True).start()
                    return
                if path == '/api/background':
                    if type(data.get('enabled')) is not bool:raise ValueError('后台状态无效')
                    with app.lock,app.workspace() as ws,ws.conn:
                        if app.closing:raise Conflict('服务正在关闭')
                        ws.conn.execute('UPDATE web_background_settings SET enabled=? WHERE id=1',(int(data['enabled']),))
                    if data['enabled']:
                        try:app.start_scheduler()
                        except RuntimeError:
                            with app.workspace() as ws,ws.conn:ws.conn.execute('UPDATE web_background_settings SET enabled=0 WHERE id=1')
                            raise Conflict('后台线程未能启动，开关已关闭；没有自动重试') from None
                    return self.respond({'ok':True})
                if path == '/api/notification-read':
                    with app.workspace() as ws,ws.conn:
                        if type(data.get('id')) is not int:raise ValueError('通知标识无效')
                        ws.conn.execute('UPDATE web_notifications SET seen=1 WHERE id=?',(data['id'],))
                    return self.respond({'ok':True})
                if path == '/api/research-job':return self.respond({'id':app.research_job(data)},202)
                if path in ('/api/research-save','/api/research-state','/api/research-watch','/api/plan-note'):
                    from lodestar.web import research
                    with app.lock,app.workspace() as ws,ws.conn:
                        project_id=data.get('project_id');research.project(ws,project_id)
                        if app.closing or f'project:{project_id}' in app.project_operations:raise Conflict('项目操作中，请稍后')
                        if path.endswith('save'):return self.respond({'id':research.save_paper(ws,project_id,data)})
                        if path.endswith('state'):
                            research.update_state(ws,project_id,data.get('paper_id'),data.get('state'));return self.respond({'ok':True})
                        if path.endswith('note'):return self.respond(research.plan_note(ws,project_id,data))
                        if data.get('watch_id'):
                            if type(data.get('enabled')) is not bool:raise ValueError('订阅状态无效')
                            changed=ws.conn.execute('UPDATE paper_watches SET enabled=? WHERE id=? AND project_id=?',(int(data['enabled']),data['watch_id'],project_id)).rowcount
                            if not changed:raise LookupError('订阅不属于此项目')
                            return self.respond({'ok':True})
                        from lodestar.agent.watch import subscribe
                        if not isinstance(data.get('query'),str) or not isinstance(data.get('terms'),list) or not all(isinstance(t,str) for t in data['terms']) or type(data.get('interval_hours')) is not int:raise ValueError('订阅参数无效')
                        return self.respond(subscribe(ws,project_id,data['query'],data['terms'],data['interval_hours']))
                if path == '/api/items':
                    return self.respond({'id':app.create(data.get('name',''), data.get('project_id'))},201)
                if path == '/api/projects':
                    return self.respond({'id': app.project(data)},201)
                if path == '/api/project-save':
                    return self.respond({'id':app.save_project(data)})
                if path == '/api/project-file':
                    from lodestar.web import project_files
                    with app.lock, app.workspace() as ws, ws.conn:
                        project_id=data.get('id')
                        projects.check_revision(ws,project_id,data.get('revision'))
                        if f'project:{project_id}' in app.project_operations: raise Conflict('项目同步中，请稍后再修改许可')
                        return self.respond(project_files.save(ws,project_id,data))
                if path in ('/api/project-local','/api/vault-note'):
                    from lodestar.web import vaults
                    with app.lock,app.workspace() as ws,ws.conn:
                        if app.closing:raise Conflict('服务正在关闭')
                        if path.endswith('project-local'):return self.respond({'id':vaults.connect(ws,data)},201)
                        project_id=data.get('id');projects.detail(ws,project_id)
                        return self.respond(vaults.save(ws,project_id,data))
                if path == '/api/project-import':
                    return self.respond({'id':app.import_project(data)})
                if path == '/api/paper-study':
                    from lodestar.web import paper_hub
                    with app.lock,app.workspace() as ws,ws.conn:
                        return self.respond(paper_hub.update(ws,data))
                if path == '/api/paper-library':
                    return self.respond(app.paper_library(data))
                item_id = data.get('item','main')
                if not isinstance(item_id,str):
                    raise ValueError('空间标识无效')
                if path == '/api/interrupt':
                    from lodestar.web.interruptions import accept
                    return self.respond(accept(app,item_id,data.get('message'),data.get('key'),data.get('reference',''),data.get('run')),202)
                if path == '/api/models':
                    return self.respond(app.models(item_id))
                if path == '/api/model-restore':
                    app.restore_model(item_id, data.get('model'))
                    return self.respond({'ok': True})
                if path == '/api/model-default':
                    return self.respond(app.select_model_default(item_id))
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
                elif path == '/api/retry':
                    return self.respond({'id':app.retry(item_id,data.get('message_id'),data.get('key'))},202)
                elif path == '/api/continue':
                    return self.respond({'id':app.continue_answer(item_id,data.get('message_id'),data.get('key'))},202)
                elif path == '/api/delete-message':
                    return self.respond(app.delete_message(item_id,data.get('message_id')))
                elif path == '/api/send':
                    return self.respond({'id':app.submit(item_id,data.get('message'),data.get('key'),
                                                           reference_text=data.get('reference',''))},202)
                else:
                    return self.respond({'error':'接口不存在'},404)
                return self.respond({'ok':True})
            except RepositoryError as error:
                return self.respond({'error':str(error),'project_id':getattr(error,'project_id',None)},502)
            except (Conflict, memory.MemoryBusy) as error:
                return self.respond({'error':str(error)},409)
            except LookupError as error:
                return self.respond({'error':str(error)},404)
            except (ValueError,TypeError):
                return self.respond({'error':'请求参数无效，请检查输入与模型设置'},400)
            except TimeoutError:
                return self.respond({'error':'请求正文读取超时，请重新提交完整请求'},408)
            except Exception:
                return self.respond({'error':'本地服务暂时无法完成此操作'},500)

        def do_GET(self):
            self.dispatch()

        def do_POST(self):
            self.dispatch(True)

    class LocalHTTPServer(ThreadingHTTPServer):
        # Windows SO_REUSEADDR can bind over a live listener, unlike Unix.
        allow_reuse_address = os.name != 'nt'

        def server_bind(self):
            if os.name == 'nt' and hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            super().server_bind()

    server = LocalHTTPServer(('127.0.0.1',port),Handler)
    server.app = app
    return server


def serve(port=8123, open_browser=True, config=None, data_dir=None, background_service=False):
    cfg = config or load_config()
    root = Path(data_dir).resolve() if data_dir is not None else Path(__file__).resolve().parents[2] / 'workspace' / 'web'
    cfg = replace(cfg,db_path=root/'web.db',workspace_dir=root,demo_replay=False)
    # Reserve the port before initialization can recover/migrate any run state.
    server = make_server(None,port)
    app = None
    try:
        with workspace_lease(cfg.db_path):
            try:
                if cfg.db_path.exists():
                    backup = root / 'backups' / f'pre-start-{time.time_ns()}-{uuid.uuid4().hex[:8]}.db'
                    backup_database(cfg.db_path, backup)
                    print(f'启动前数据库备份: {backup}', flush=True)
                app = WebApp(cfg)
                server.app = app
                app.start_scheduler()
                if background_service:
                    from lodestar.web.background import marker
                    record=marker(root)
                    record.write_text(json.dumps({'instance_id':app.instance_id,'token':app.token,'port':server.server_port,'pid':os.getpid()}),encoding='utf-8')
                    if os.name!='nt':record.chmod(0o600)
                url = f'http://127.0.0.1:{server.server_port}'
                print(f'Lodestar 本地工作空间: {url}',flush=True)
                if open_browser:
                    webbrowser.open(url)
                try:
                    server.serve_forever()
                except KeyboardInterrupt:
                    pass
            finally:
                try:
                    if app is not None:
                        if app.workers:
                            print('正在停止运行；已发出的网络请求可能需等待超时。', flush=True)
                        app.close()
                        if background_service:
                            from lodestar.web.background import marker,_record
                            value=_record(root)
                            if value and value['instance_id']==app.instance_id:marker(root).unlink(missing_ok=True)
                finally:
                    server.server_close()
    finally:
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8123)
    parser.add_argument('--no-browser',action='store_true')
    parser.add_argument('--data-dir', type=Path, help='独立本地数据目录（默认 workspace/web）')
    args = parser.parse_args()
    serve(args.port,not args.no_browser,data_dir=args.data_dir)
