"""Version-bound local file permission and project isolation."""
from pathlib import Path
import tempfile,unittest
from unittest.mock import Mock
from lodestar.config import Config
from lodestar.web.server import WebApp
from lodestar.web import project_files as files,projects
from lodestar import project_workspaces as git
from lodestar.agent.project_evidence import collect

class FilePermissionTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name)
  self.app=WebApp(Config(llm_mode='mock',search_mode='mock',workspace_dir=self.root,db_path=self.root/'web.db'),llm_factory=Mock(side_effect=AssertionError('no inference')))
  self.addCleanup(self.app.close)
  self.pid=self.app.save_project({'name':'Test project'})
  self.other=self.app.save_project({'name':'Other project'})
  self.path=git.workspace_path(self.root,'c'*32);self.path.mkdir(parents=True)
  for args in [('init','-q','-b','master'),('config','user.name','Fixture'),('config','user.email','fixture@example.invalid')]:git._run(self.path,*args)
  (self.path/'memory.py').write_text('MEMORY_PRIVATE_CANARY = 123\n',encoding='utf-8')
  (self.path/'.env').write_text('PRIVATE_KEY_CANARY',encoding='utf-8')
  git._run(self.path,'add','memory.py');git._run(self.path,'commit','-qm','fixture')
  with self.app.workspace() as ws,ws.conn:ws.conn.execute('INSERT INTO web_project_checkouts VALUES(?,?)',(self.pid,'c'*32))
 def allow(self):
  with self.app.workspace() as ws,ws.conn:
   packet=files.preview(ws,self.pid,'memory.py');files.save(ws,self.pid,{**packet,'approved':True})
 def test_no_implicit_export_and_local_preview_then_explicit_snapshot(self):
  with self.app.workspace() as ws:
   self.assertEqual(files.approved(ws,self.pid),[])
   packet=files.preview(ws,self.pid,'memory.py');self.assertIn('MEMORY_PRIVATE_CANARY',packet['content'])
   self.assertEqual(files.approved(ws,self.pid),[])
  self.allow()
  with self.app.workspace() as ws:
   self.assertEqual(len(files.approved(ws,self.pid,'memory')),1)
   self.assertEqual(files.approved(ws,self.other),[])
   result=collect(ws,'memory',[],self.pid,live=True)
   self.assertEqual(result['documents'][0]['approval'],'explicit_file_snapshot')
 def test_changed_content_or_commit_stops_delivery_and_stale_approval(self):
  self.allow()
  (self.path/'memory.py').write_text('changed memory content',encoding='utf-8')
  with self.app.workspace() as ws:
   self.assertEqual(files.approved(ws,self.pid),[])
  self.allow()
  git._run(self.path,'commit','--allow-empty','-qm','new version')
  with self.app.workspace() as ws:
   self.assertEqual(files.approved(ws,self.pid),[])
 def test_paths_secrets_untracked_and_binary_are_not_previewed(self):
  for value in ['../memory.py','C:/secret.txt','.env','../.git/config','memory.py/../../outside.py','private_key.txt']:
   with self.subTest(path=value),self.app.workspace() as ws:
    with self.assertRaises(ValueError):files.preview(ws,self.pid,value)
  (self.path/'untracked.py').write_text('untracked',encoding='utf-8')
  with self.app.workspace() as ws:
   with self.assertRaises(git.RepositoryError):files.preview(ws,self.pid,'untracked.py')
   self.assertEqual([f['path'] for f in files.catalog(ws,self.pid)['files']],['memory.py'])
 def test_revoke_removes_snapshot(self):
  self.allow()
  with self.app.workspace() as ws,ws.conn:
   files.save(ws,self.pid,{'path':'memory.py','approved':False})
   self.assertEqual(files.approved(ws,self.pid),[])
 def test_preview_budget_and_changed_preview_rejected(self):
  (self.path/'memory.py').write_text('x'*8000,encoding='utf-8')
  with self.app.workspace() as ws,ws.conn:
   packet=files.preview(ws,self.pid,'memory.py')
   self.assertTrue(packet['truncated']);self.assertEqual(len(packet['content']),4000)
   (self.path/'memory.py').write_text('changed',encoding='utf-8')
   with self.assertRaises(Exception):files.save(ws,self.pid,{**packet,'approved':True})

 def test_active_project_run_blocks_snapshot_permission_change(self):
  self.app.update('main',{'project_id':self.pid})
  with self.app.workspace() as ws,ws.conn:
   packet=files.preview(ws,self.pid,'memory.py')
   ws.conn.execute("INSERT INTO web_runs(id,item_id,request_key,status,message,config) VALUES('active','main','active','running','test','{}')")
  with self.app.workspace() as ws,ws.conn:
   from lodestar.web.memory import MemoryBusy
   with self.assertRaises(MemoryBusy):files.save(ws,self.pid,{**packet,'approved':True})
   self.assertEqual(files.approved(ws,self.pid),[])
 def test_tracked_binary_and_large_files_are_rejected(self):
  (self.path/'binary.py').write_bytes(b'\x00payload')
  (self.path/'large.py').write_bytes(b'x'*200001)
  git._run(self.path,'add','binary.py','large.py')
  with self.app.workspace() as ws:
   for path in ['binary.py','large.py']:
    with self.subTest(path=path),self.assertRaises(ValueError):files.preview(ws,self.pid,path)

 def test_approved_web_file_flows_through_assessment_and_plan_and_revocation(self):
  self.allow()
  from lodestar.web import research
  from lodestar.fixtures import all_papers
  from lodestar.agent import candidate
  from lodestar.llm import LLMClient
  with self.app.workspace() as ws,ws.conn:
   paper=research.save_paper(ws,self.pid,{'url':all_papers()[0]['url']})
   candidate.read(ws,self.pid,paper)
   client=LLMClient(ws.config)
   try:
    assessment=candidate.assess(ws,client,self.pid,paper,'memory')
    self.assertEqual(assessment['status'],'assessed')
    plan=candidate.plan(ws,client,self.pid,paper,'memory')
    self.assertTrue(plan['contract_valid'],plan)
    self.assertEqual(plan['project_documents'][0]['approval'],'explicit_file_snapshot')
    files.save(ws,self.pid,{'path':'memory.py','approved':False})
    blocked=candidate.plan(ws,client,self.pid,paper,'memory')
    self.assertEqual(blocked['status'],'needs_reassessment');self.assertEqual(blocked['model_calls'],0)
   finally:client.close()
