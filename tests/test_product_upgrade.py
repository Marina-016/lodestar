"""Offline acceptance for continuity, durable interruptions and real local Markdown."""
import json,tempfile,threading,time,unittest
from pathlib import Path
from lodestar.config import Config
from lodestar.memory import repo
from lodestar.agent import continuity
from lodestar.web import vaults,project_files,projects,interruptions
from lodestar.web.server import WebApp,Conflict

class LocalLLM:
    started=threading.Event();release=threading.Event();calls=[];hold=False;cooperative=True
    def __init__(self,config):pass
    def close(self):pass
    def dialogue_step(self,system,messages,tools,on_text=None,**kwargs):
        type(self).calls.append(messages)
        if self.hold and len(self.calls)==1:
            if on_text:on_text('已有的部分回答。')
            self.started.set()
            while not self.release.wait(.01):
                if self.cooperative and on_text:on_text('')
        text='已按最新问题回答。'
        if on_text:on_text(text)
        return {'role':'assistant','content':[{'type':'text','text':text}]}

class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.cfg=Config(llm_mode='mock',search_mode='mock',model='offline-upgrade',workspace_dir=self.root,db_path=self.root/'web.db')
        LocalLLM.started=threading.Event();LocalLLM.release=threading.Event();LocalLLM.calls=[];LocalLLM.hold=False;LocalLLM.cooperative=True
        self.app=WebApp(self.cfg,llm_factory=LocalLLM)
    def tearDown(self):
        LocalLLM.release.set();self.app.close();self.tmp.cleanup()
    def wait(self,predicate):
        until=time.monotonic()+8
        while time.monotonic()<until:
            value=predicate()
            if value:return value
            time.sleep(.01)
        self.fail('local worker did not settle')
    def test_long_conversation_keeps_anchored_constraints_and_latest_correction(self):
        with self.app.workspace() as ws,ws.conn:
            cid=self.app.item(ws,'main')['conversation_id']
            old=repo.add_message(ws.conn,cid,'user','Use Python only. Do not upload private files.')
            continuity.initialize(ws.conn)
            for i in range(45):
                repo.add_message(ws.conn,cid,'assistant','Completed explanation '+str(i))
                repo.add_message(ws.conn,cid,'user','Next detail '+str(i))
            now=repo.add_message(ws.conn,cid,'user','Correction: use Rust, keep the no-upload constraint.')
            update=continuity.tool(ws.conn,cid,now['id'],0)['update_conversation']['handler']
            update({'entries':[{'key':'language','kind':'constraint','content':'Use Python','source_messages':[old['id']],'status':'superseded'},
               {'key':'language','kind':'correction','content':'Use Rust','source_messages':[now['id']],'status':'active'},
               {'key':'privacy','kind':'constraint','content':'Do not upload private files','source_messages':[old['id']],'status':'active'}]})
            packet=continuity.packet(ws.conn,cid,now['id']+1,0,'next step')
            self.assertIn('Use Rust',json.dumps(packet));self.assertNotIn('Use Python',json.dumps(packet['working_state']))
            self.assertTrue(any(a['message_id']==old['id'] for a in packet['earlier_user_requests']))
            retired=continuity.packet(ws.conn,cid,now['id']+1,old['id'],'next')
            self.assertNotIn('privacy',json.dumps(retired['working_state']))
    def test_working_state_rejects_foreign_sources_and_resolves_open_questions(self):
        other=self.app.create('Other')
        with self.app.workspace() as ws,ws.conn:
            cid=self.app.item(ws,'main')['conversation_id'];foreign=repo.add_message(ws.conn,self.app.item(ws,other)['conversation_id'],'user','private other task')
            own=repo.add_message(ws.conn,cid,'user','Explain the remaining method')
            update=continuity.tool(ws.conn,cid,own['id'],0)['update_conversation']['handler']
            entry={'key':'pending','kind':'open_question','content':'Explain the remaining method','source_messages':[foreign['id']],'status':'active'}
            with self.assertRaises(ValueError):update({'entries':[entry]})
            entry['source_messages']=[own['id']];update({'entries':[entry]});entry['status']='resolved';update({'entries':[entry]})
            self.assertEqual(continuity.packet(ws.conn,cid,own['id']+1,0,'continue')['working_state'],[])
    def test_interrupt_cancels_old_turn_and_starts_exactly_one_followup(self):
        LocalLLM.hold=True
        old=self.app.submit('main','Original unfinished question','original')
        self.assertTrue(LocalLLM.started.wait(5))
        first=interruptions.accept(self.app,'main','Correction: explain only one example.','interrupt',run=old)
        duplicate=interruptions.accept(self.app,'main','Correction: explain only one example.','interrupt',run=old)
        self.assertEqual(first['id'],duplicate['id'])
        value=self.wait(lambda:self.app.state() if self.app.state()['run']['id']!=old and self.app.state()['run']['status']=='completed' else None)
        self.assertEqual(len(LocalLLM.calls),2)
        self.assertEqual([m['content'] for m in value['messages'] if m['role']=='user'],['Original unfinished question','Correction: explain only one example.'])
        with self.app.workspace() as ws:
            self.assertEqual(ws.conn.execute('SELECT status FROM web_runs WHERE id=?',(old,)).fetchone()[0],'cancelled')
        self.assertIn('Original unfinished question',json.dumps(LocalLLM.calls[-1],ensure_ascii=False))
    def test_interrupt_keeps_the_original_external_retrieval_boundary(self):
        LocalLLM.hold=True
        old=self.app.submit('main','Use local context only','original',policy={'external_retrieval':'blocked'})
        self.assertTrue(LocalLLM.started.wait(5))
        interruptions.accept(self.app,'main','Explain it more simply','change',run=old)
        self.wait(lambda:self.app.state()['run']['id']!=old and self.app.state()['run']['status']=='completed')
        self.assertEqual(json.loads(self.app.state()['run']['policy'])['external_retrieval'],'blocked')
    def test_retired_sources_and_malformed_old_metadata_do_not_revive_state(self):
        with self.app.workspace() as ws,ws.conn:
            cid=self.app.item(ws,'main')['conversation_id']
            user=repo.add_message(ws.conn,cid,'user','Temporary constraint')
            row=repo.add_message(ws.conn,cid,'assistant','An old reply')
            continuity.tool(ws.conn,cid,user['id'],0)['update_conversation']['handler']({'entries':[{'key':'old','kind':'constraint','content':'Temporary constraint','source_messages':[user['id']],'status':'active'}]})
            ws.conn.execute('UPDATE messages SET metadata=? WHERE id=?',('invalid legacy JSON',row['id']))
            ws.conn.execute('DELETE FROM messages WHERE id=?',(user['id'],))
            value=continuity.packet(ws.conn,cid,row['id']+1,0,'next')
            self.assertEqual(value['working_state'],[])
    def test_stop_removes_pending_followup_without_replaying_it(self):
        LocalLLM.hold=True;LocalLLM.cooperative=False
        old=self.app.submit('main','Original','original');self.assertTrue(LocalLLM.started.wait(5))
        interruptions.accept(self.app,'main','Supplement','interrupt',run=old)
        with self.assertRaises(Conflict):self.app.submit('main','Racing message','race')
        self.app.cancel('main',old);LocalLLM.release.set()
        self.wait(lambda:self.app.events(old,0)['run']['status']=='cancelled')
        self.assertEqual(len(LocalLLM.calls),1)
        self.assertIsNone(self.app.state()['followup'])
    def test_restart_preserves_pending_text_without_autostart(self):
        with self.app.workspace() as ws,ws.conn:
            ws.conn.execute("INSERT INTO web_followups(id,item_id,request_key,message,reference_text) VALUES('pending','main','pending','Keep this correction','')")
        self.app.close();self.app=WebApp(self.cfg,llm_factory=LocalLLM)
        self.assertEqual(self.app.state()['followup']['status'],'interrupted')
        self.assertEqual(self.app.state()['followup']['message'],'Keep this correction');self.assertEqual(LocalLLM.calls,[])
    def vault(self):
        base=self.root/'notes';base.mkdir();(base/'.obsidian').mkdir();(base/'Topic.md').write_text('# Original\nEvidence note.',encoding='utf-8')
        with self.app.workspace() as ws,ws.conn:p=vaults.connect(ws,{'path':str(base),'name':'Notes'})
        return base,p
    def test_vault_connect_edit_version_conflict_and_explicit_export(self):
        base,p=self.vault()
        with self.app.workspace() as ws,ws.conn:
            self.assertEqual(projects.detail(ws,p)['local_vault']['kind'],'obsidian')
            self.assertEqual(vaults.catalog(ws,p,'Evidence')['files'][0]['path'],'Topic.md')
            note=vaults.read(ws,p,'Topic.md');revision=projects.detail(ws,p)['revision']
            self.assertEqual(project_files.approved(ws,p),[])
            project_files.save(ws,p,{**note,'approved':True})
            self.assertEqual(project_files.approved(ws,p)[0]['commit_sha'],'local')
            revision=projects.detail(ws,p)['revision']
            (base/'Topic.md').write_text('Changed in Obsidian',encoding='utf-8')
            self.assertEqual(project_files.approved(ws,p),[])
            with self.assertRaises(Conflict):vaults.save(ws,p,{'path':'Topic.md','content':'overwrite','digest':note['digest'],'revision':revision})
            current=vaults.read(ws,p,'Topic.md')
            vaults.save(ws,p,{**current,'content':'Saved in Lodestar','revision':revision})
            self.assertEqual((base/'Topic.md').read_text(),'Saved in Lodestar')
            self.assertTrue(list((self.root/'vault-backups'/str(p)).glob('*.md')))
            self.assertEqual(project_files.approved(ws,p),[])
    def test_vault_paths_hidden_files_and_existing_files_are_protected(self):
        base,p=self.vault()
        with self.app.workspace() as ws,ws.conn:
            for path in ['../outside.md','.obsidian/config.md','C:/outside.md','notes\\bad.md','secret.md']:
                with self.subTest(path=path),self.assertRaises(ValueError):vaults.read(ws,p,path)
            rev=projects.detail(ws,p)['revision']
            with self.assertRaises(Conflict):vaults.save(ws,p,{'path':'Topic.md','content':'overwrite','digest':'','revision':rev})
            vaults.save(ws,p,{'path':'Research/New.md','content':'# New','digest':'','revision':rev})
            self.assertEqual((base/'Research/New.md').read_text(),'# New')
            self.assertNotIn('.obsidian',json.dumps(vaults.catalog(ws,p)))

if __name__=='__main__':unittest.main()
