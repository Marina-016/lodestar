"""Offline end-to-end checks for research, transcript scope and durable review records."""
import json,time,unittest,threading,tempfile
from pathlib import Path
from unittest.mock import Mock,patch
import requests
from lodestar.config import Config
from lodestar.web.server import WebApp,make_server,Conflict
from lodestar.web import research,history
from lodestar.web.background import Scheduler,notify,view
from lodestar.agent import watch
from lodestar.memory import repo
from lodestar.fixtures import all_papers

class WorkbenchTests(unittest.TestCase):
 def setUp(self):
  tmp=tempfile.TemporaryDirectory(prefix='lodestar-workbench-');self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
  self.cfg=Config(llm_mode='mock',search_mode='mock',model_calls_disabled=True,workspace_dir=self.root,db_path=self.root/'web.db')
  self.factory=Mock(side_effect=AssertionError('No model call expected'))
  self.app=WebApp(self.cfg,llm_factory=self.factory);self.addCleanup(self.app.close)
  self.p=self.app.save_project({'name':'Project P'});self.q=self.app.save_project({'name':'Project Q'})
  self.a=self.app.create('P task',self.p);self.b=self.app.create('Other P task',self.p);self.c=self.app.create('Q task',self.q)
 def seed(self,item,text,role='user'):
  with self.app.workspace() as ws,ws.conn:return repo.add_message(ws.conn,self.app.item(ws,item)['conversation_id'],role,text)['id']
 def paper(self,project=None):
  with self.app.workspace() as ws,ws.conn:return research.save_paper(ws,project or self.p,{'url':all_papers()[0]['url'],'title':'Memory method'})
 def wait(self,id):
  deadline=time.monotonic()+10
  while time.monotonic()<deadline:
   with self.app.workspace() as ws:r=dict(ws.conn.execute('SELECT * FROM web_research_jobs WHERE id=?',(id,)).fetchone())
   if r['status'] not in ('queued','running'):
    # Owned thread cleanup is distinct from the persisted terminal state.
    with self.app.lock:workers=list(self.app.project_workers)
    for worker in workers:worker.join(10)
    return r
   time.sleep(.02)
  self.fail('research job did not terminate')
 def test_history_literal_query_pagination_and_owner(self):
  for _ in range(24):self.seed(self.a,'100% memory result')
  self.seed(self.b,'plain memory result')
  with self.app.workspace() as ws:
   r=history.search(ws,'%');self.assertEqual(r['total'],24);self.assertEqual(len(r['matches']),20)
   self.assertEqual(len(history.search(ws,'%',page=2)['matches']),4)
   self.assertIn('/history?',r['matches'][0]['url'])
   self.assertEqual(history.search(ws,'_',project_id=self.p)['total'],0)
 def test_model_history_excludes_retired_other_project_and_unbound_tasks(self):
  old=self.seed(self.a,'CANARY memory old');valid=self.seed(self.b,'CANARY memory sibling');self.seed(self.c,'CANARY private Q')
  with self.app.workspace() as ws,ws.conn:
   ws.conn.execute('UPDATE web_items SET context_after=? WHERE id=?',(old,self.a))
   result=history.search(ws,'CANARY',model_item=self.app.item(ws,self.a))
   self.assertEqual([m['id'] for m in result['matches']],[valid])
   self.assertEqual(history.search(ws,'CANARY',model_item=self.app.item(ws,'main'))['total'],0)
   self.assertEqual(history.search(ws,'CANARY',project_id=self.p)['total'],2)
 def test_original_message_cannot_be_read_with_wrong_task_or_foreign_owner(self):
  id=self.seed(self.a,'original message')
  with self.app.workspace() as ws,ws.conn:
   with self.assertRaises(LookupError):history.message(ws,self.b,id)
   self.assertEqual(history.message(ws,self.a,id)['content'],'original message')
   ws.conn.execute("UPDATE web_items SET owner='other' WHERE id=?",(self.a,))
   self.assertEqual(history.search(ws,'original')['total'],0)
 def test_inbox_dedup_version_isolation_and_user_read_state(self):
  id=self.paper();self.assertEqual(self.paper(),id)
  with self.app.workspace() as ws,ws.conn:
   with self.assertRaises(ValueError):research.save_paper(ws,self.p,{'url':all_papers()[0]['url']+'v2'})
   with self.assertRaises(ValueError):research.update_state(ws,self.q,id,'read')
   research.update_state(ws,self.p,id,'read');r=research.catalog(ws,self.p)
   self.assertIsNone(r['papers'][0]['reading']);self.assertEqual(r['papers'][0]['state'],'read')
   self.assertEqual(research.catalog(ws,self.q)['total'],0)
 def test_watch_tick_does_not_run_other_projects_queries(self):
  with self.app.workspace() as ws:
   watch.subscribe(ws,self.p,'P public query',['memory']);watch.subscribe(ws,self.q,'Q public query',['memory'])
   discovery=Mock(return_value={'status':'ok','sources':[],'mode':'mock'})
   result=watch.tick(ws,project_id=self.p,discovery=discovery)
   self.assertEqual(len(result['runs']),1)
   self.assertEqual({call.args[1] for call in discovery.call_args_list},{'P public query'})
 def test_real_offline_read_job_idempotency_notifications_and_no_model(self):
  paper=self.paper();data={'project_id':self.p,'paper_id':paper,'action':'read','key':'read-once'}
  id=self.app.research_job(data);result=self.wait(id)
  self.assertEqual(result['status'],'completed');self.assertEqual(self.app.research_job(data),id)
  self.factory.assert_not_called()
  with self.app.workspace() as ws:
   self.assertIsNotNone(research.detail(ws,self.p,paper)['reading'])
   self.assertEqual(view(ws)['unread'],1)
   self.assertEqual(ws.conn.execute('SELECT COUNT(*) FROM learning_events').fetchone()[0],0)
 def test_missing_project_evidence_prevents_model_construction(self):
  paper=self.paper();self.wait(self.app.research_job({'project_id':self.p,'paper_id':paper,'action':'read','key':'read'}))
  r=self.wait(self.app.research_job({'project_id':self.p,'paper_id':paper,'action':'assess','goal':'memory','key':'assess'}))
  self.assertEqual(r['status'],'error');self.assertEqual(json.loads(r['result'])['model_calls'],0);self.factory.assert_not_called()
 def test_failed_worker_start_releases_slot_and_keeps_failure(self):
  paper=self.paper()
  with patch('lodestar.web.server.threading.Thread.start',side_effect=RuntimeError('PRIVATE_CANARY')):
   with self.assertRaises(Conflict):self.app.research_job({'project_id':self.p,'paper_id':paper,'action':'read','key':'start-fail'})
  self.assertNotIn('project:'+str(self.p),self.app.project_operations)
  with self.app.workspace() as ws:
   r=research.catalog(ws,self.p)['jobs'][0];self.assertEqual(r['status'],'error');self.assertNotIn('PRIVATE_CANARY',r['result'])
 def test_manual_plan_revision_and_results_are_not_execution_or_mastery(self):
  paper=self.paper();packet={'paper_id':paper,'title':'Try memory','hypothesis':'May improve retrieval','protocol':'Freeze 10 cases; compare baseline and candidate','observation':'Not run'}
  with self.app.workspace() as ws,ws.conn:
   r=research.plan_note(ws,self.p,packet);self.assertEqual(r['execution_status'],'not_executed_by_lodestar')
   research.plan_note(ws,self.p,{**packet,'note_id':r['id'],'revision':1,'decision':'adopt'})
   with self.assertRaises(ValueError):research.plan_note(ws,self.p,{**packet,'note_id':r['id'],'revision':1})
   with self.assertRaises(ValueError):research.plan_note(ws,self.q,packet)
   self.assertEqual(ws.conn.execute('SELECT COUNT(*) FROM paper_candidate_experiments').fetchone()[0],0)
   self.assertEqual(ws.conn.execute('SELECT COUNT(*) FROM learning_events').fetchone()[0],0)
 def test_scheduler_default_off_and_unchanged_checks_stay_quiet(self):
  with self.app.workspace() as ws:watch.subscribe(ws,self.p,'memory',['memory'])
  scheduler=Scheduler(self.app);scheduler.tick()
  with self.app.workspace() as ws:self.assertEqual(ws.conn.execute('SELECT COUNT(*) FROM web_research_jobs').fetchone()[0],0)
  with self.app.workspace() as ws,ws.conn:ws.conn.execute('UPDATE web_background_settings SET enabled=1')
  with patch('lodestar.agent.watch.tick',return_value={'runs':[{'status':'ok','new_recommendations':0}]}):
   scheduler.tick()
   with self.app.workspace() as ws:id=ws.conn.execute('SELECT id FROM web_research_jobs').fetchone()[0]
   self.wait(id);scheduler.tick()
  with self.app.workspace() as ws:
   self.assertEqual(ws.conn.execute('SELECT COUNT(*) FROM web_research_jobs').fetchone()[0],1)
   self.assertEqual(view(ws)['unread'],0)
 def test_reopen_marks_jobs_interrupted_without_replay(self):
  with self.app.workspace() as ws,ws.conn:
   ws.conn.execute("INSERT INTO web_research_jobs(id,project_id,action,status,request_key,payload,config) VALUES('interrupted',?,'plan','running','pending','{}','{}')",(self.p,))
  reopened=WebApp(self.cfg,llm_factory=self.factory)
  try:
   with reopened.workspace() as ws:
    self.assertEqual(ws.conn.execute("SELECT status FROM web_research_jobs WHERE id='interrupted'").fetchone()[0],'interrupted')
    self.assertEqual(view(ws)['notifications'][0]['status'],'interrupted')
   self.factory.assert_not_called()
  finally:reopened.close()
 def test_http_routes_and_authentication_no_hidden_calls(self):
  server=make_server(self.app,0);thread=threading.Thread(target=server.serve_forever);thread.start()
  try:
   base='http://127.0.0.1:'+str(server.server_port)
   with requests.Session() as session:
    session.trust_env=False
    for path in ['/research','/history','/workbench.mjs','/api/background','/api/service']:
     self.assertEqual(session.get(base+path,timeout=5).status_code,200,path)
    r=session.post(base+'/api/research-save',json={'project_id':self.p,'url':'https://arxiv.org/abs/2601.00001'},timeout=5);self.assertEqual(r.status_code,403)
    r=session.post(base+'/api/research-save',headers={'X-Lodestar-Token':self.app.token},json={'project_id':self.p,'url':'https://arxiv.org/abs/2601.00001'},timeout=5);self.assertEqual(r.status_code,200)
    r=session.get(base+'/api/history?query=none',timeout=5);self.assertEqual(r.json()['total'],0)
   self.factory.assert_not_called()
  finally:server.shutdown();server.server_close();thread.join(5)

 def test_unknown_offline_paper_is_failed_read_not_success(self):
  with self.app.workspace() as ws,ws.conn:paper=research.save_paper(ws,self.p,{'url':'https://arxiv.org/abs/2601.00001v1'})
  r=self.wait(self.app.research_job({'project_id':self.p,'paper_id':paper,'action':'read','key':'unknown-fixture'}))
  self.assertEqual(r['status'],'error')
  with self.app.workspace() as ws:self.assertIsNone(research.detail(ws,self.p,paper)['reading'])

 def test_thread_construction_failure_keeps_attempt_without_owned_worker(self):
  paper=self.paper()
  with patch('lodestar.web.server.threading.Thread',side_effect=RuntimeError('constructor failure')):
   with self.assertRaises(Conflict):self.app.research_job({'project_id':self.p,'paper_id':paper,'action':'read','key':'constructor-failure'})
  self.assertEqual(self.app.project_operations,{})
  with self.app.workspace() as ws:self.assertEqual(research.catalog(ws,self.p)['jobs'][0]['status'],'error')

 def test_failed_scheduler_start_does_not_leave_unjoinable_thread(self):
  with patch('lodestar.web.server.threading.Thread.start',side_effect=RuntimeError('failed scheduler')):
   with self.assertRaises(RuntimeError):self.app.start_scheduler()
  self.assertIsNone(self.app.scheduler)
  self.app.close()
