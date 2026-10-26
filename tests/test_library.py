"""Project/long-term memory management: offline fixtures, no cloud inference."""
import base64
from contextlib import closing
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import requests

from lodestar.agent.project_evidence import collect
from lodestar.config import Config
from lodestar.memory import repo
from lodestar.project_sources import RepositoryError, fetch_snapshot, repository_url
from lodestar.web import memory, projects
from lodestar.web.server import Conflict, WebApp, make_server


def snapshot(sha='a'*40, content='README fixture evidence'):
    return {'repository':'https://github.com/example/research','name':'example/research',
            'description':'Imported fixture description','branch':'master','commit_sha':sha,
            'readme_path':'README.md','readme':content,'truncated':False}


class LibraryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='lodestar-library-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.cfg = Config(llm_mode='mock',search_mode='mock',workspace_dir=self.root,db_path=self.root/'web.db')
        self.factory = Mock(side_effect=AssertionError('No model construction allowed'))
        self.app = WebApp(self.cfg,llm_factory=self.factory)
        self.addCleanup(self.app.close)

    def project(self, name='My project', **values):
        return self.app.save_project({'name':name,'description':'USER_PROJECT_DESCRIPTION',**values})

    def entry(self, project_id):
        with self.app.workspace() as ws:
            return projects.detail(ws,project_id)

    def test_pages_and_reads_do_not_construct_client(self):
        self.assertEqual(self.app.library()['projects'],[])
        with self.app.workspace() as ws:
            self.assertEqual(memory.catalog(ws)['total'],0)
        self.factory.assert_not_called()

    def test_metadata_requires_explicit_approval_and_does_not_grant_code(self):
        project_id = self.project()
        with self.app.workspace() as ws:
            repo.replace_project_documents(ws.conn,project_id,[{'path':'private.py','content':'PRIVATE_CODE_CANARY'}])
            self.assertNotIn('USER_PROJECT_DESCRIPTION',json.dumps(collect(ws,'private',[],project_id,live=True)))
        old = self.entry(project_id)
        self.app.save_project({**old,'allow_model_description':True})
        with self.app.workspace() as ws:
            context = collect(ws,'private',[],project_id,live=True)
        self.assertEqual(context['project']['description'],'USER_PROJECT_DESCRIPTION')
        self.assertEqual(context['status'],'needs_export_scope')
        self.assertEqual(context['documents'],[])
        self.assertNotIn('PRIVATE_CODE_CANARY',json.dumps(context))

    def test_project_revision_and_active_runs_prevent_stale_mutation(self):
        project_id=self.project()
        self.app.update('main',{'project_id':project_id})
        old=self.entry(project_id)
        with self.app.workspace() as ws, ws.conn:
            ws.conn.execute("INSERT INTO web_runs(id,item_id,request_key,status,message,config) VALUES('active','main','active','running','fixture','{}')")
        with self.assertRaises(Conflict): self.app.save_project({**old,'description':'must not change'})
        self.assertEqual(self.entry(project_id)['description'],old['description'])
        with self.app.workspace() as ws, ws.conn: ws.conn.execute("UPDATE web_runs SET status='completed'")
        self.app.save_project({**old,'description':'new description'})
        with self.assertRaises(Conflict): self.app.save_project(old)
        self.assertEqual(self.entry(project_id)['description'],'new description')

    def test_readme_is_versioned_not_memory_and_new_version_revokes_permission(self):
        with self.app.workspace() as ws, ws.conn:
            project_id=projects.apply_snapshot(ws,snapshot())
        old=self.entry(project_id)
        self.assertFalse(old['repository']['allow_model_readme'])
        self.app.save_project({**old,'allow_model_readme':True})
        with self.app.workspace() as ws:
            context=collect(ws,'evidence',[],project_id,live=True)
            self.assertEqual(context['documents'][0]['content'],'README fixture evidence')
            self.assertEqual(context['documents'][0]['commit_sha'],'a'*40)
            self.assertEqual(memory.catalog(ws)['total'],0)
        updated=self.entry(project_id)
        with self.app.workspace() as ws, ws.conn:
            projects.apply_snapshot(ws,snapshot('b'*40,'new unapproved README'),project_id=project_id,revision=updated['revision'])
        after=self.entry(project_id)
        self.assertFalse(after['repository']['allow_model_readme'])
        self.assertEqual(len(after['versions']),2)
        with self.app.workspace() as ws:
            self.assertEqual(collect(ws,'new',[],project_id,live=True)['documents'],[])

    def test_same_snapshot_preserves_permission_but_not_user_description_overwrite(self):
        with self.app.workspace() as ws, ws.conn: project_id=projects.apply_snapshot(ws,snapshot())
        old=self.entry(project_id)
        self.app.save_project({**old,'description':'MY_EDIT','allow_model_readme':True})
        old=self.entry(project_id)
        with self.app.workspace() as ws, ws.conn:
            projects.apply_snapshot(ws,snapshot(),project_id=project_id,revision=old['revision'])
        updated=self.entry(project_id)
        self.assertTrue(updated['repository']['allow_model_readme'])
        self.assertEqual(updated['description'],'MY_EDIT')
        self.assertEqual(len(updated['versions']),1)

    def test_failed_clone_keeps_import_visible_and_can_resume(self):
        with patch('lodestar.web.server.fetch_snapshot',return_value=snapshot()), patch(
                'lodestar.project_workspaces.clone',side_effect=RepositoryError('fixture clone failed')) as clone:
            with self.assertRaises(RepositoryError) as error:
                self.app.import_project({'url':snapshot()['repository']})
        project_id=error.exception.project_id
        self.assertEqual(clone.call_count,1)
        self.assertFalse(self.entry(project_id)['has_workspace'])
        with patch('lodestar.web.server.fetch_snapshot',return_value=snapshot()), patch(
                'lodestar.project_workspaces.clone',return_value='a'*32):
            self.app.import_project({'id':project_id,'revision':self.entry(project_id)['revision']})
        self.assertTrue(self.entry(project_id)['has_workspace'])
        self.factory.assert_not_called()

    def test_duplicate_import_never_reclones_or_overwrites(self):
        with patch('lodestar.web.server.fetch_snapshot',return_value=snapshot()), patch(
                'lodestar.project_workspaces.clone',return_value='a'*32) as clone:
            project_id=self.app.import_project({'url':snapshot()['repository']})
            with self.assertRaises(Conflict): self.app.import_project({'url':snapshot()['repository']})
        self.assertEqual(clone.call_count,1)
        self.assertEqual(len(self.app.library()['projects']),1)
        self.assertEqual(self.entry(project_id)['repository']['commit_sha'],'a'*40)

    def test_project_operation_prevents_bound_send_and_edits(self):
        project_id=self.project()
        self.app.update('main',{'project_id':project_id})
        self.app.project_operations[f'project:{project_id}']=threading.current_thread()
        try:
            with self.assertRaises(Conflict): self.app.submit('main','must not run','operation')
            with self.assertRaises(Conflict): self.app.save_project(self.entry(project_id))
        finally: self.app.project_operations.clear()
        self.factory.assert_not_called()

    def test_memory_catalog_scope_pagination_literal_search_and_history_erasure(self):
        project_id=self.project()
        task=self.app.create('Scoped task',project_id)
        self.app.note('main',{'content':'literal_100%','scope':'project','project_id':project_id})
        self.app.note('main',{'content':'task only','scope':'task','item_id':task})
        with self.app.workspace() as ws, ws.conn:
            for i in range(42): memory.edit(ws,self.app.item(ws,'main'),{'content':f'Preference {i}'},catalog=True)
        with self.app.workspace() as ws:
            result=memory.catalog(ws,query='100%')
            self.assertEqual(result['total'],1)
            note=result['notes'][0]
            self.assertEqual(memory.catalog(ws,scope='task')['notes'][0]['item_id'],task)
            self.assertEqual(len(memory.catalog(ws)['notes']),40)
            self.assertEqual(len(memory.catalog(ws,page=2)['notes']),4)
        self.app.note('main',{'id':note['id'],'revision':note['revision'],'content':'updated'})
        with self.app.workspace() as ws: self.assertEqual(len(memory.history(ws,note['id'])['revisions']),2)
        self.app.note('main',{'id':note['id'],'revision':2,'status':'deleted'})
        with self.app.workspace() as ws:
            with self.assertRaises(LookupError): memory.history(ws,note['id'])
            self.assertTrue(all(r[0] is None for r in ws.conn.execute('SELECT content FROM assistant_memory_revisions WHERE memory_id=?',(note['id'],))))

    def test_memory_catalog_never_exposes_other_user_and_task_target_is_owned(self):
        with self.app.workspace() as ws, ws.conn:
            from lodestar.memory import assistant
            assistant.save(ws.conn,'foreign-user',content='FOREIGN_CANARY')
        with self.app.workspace() as ws:
            self.assertNotIn('FOREIGN_CANARY',json.dumps(memory.catalog(ws)))
        with self.assertRaises(ValueError): self.app.note('main',{'content':'no','scope':'task','item_id':'foreign-task'})

    def test_http_pages_assets_and_mutations_preserve_auth(self):
        server=make_server(self.app,0)
        worker=threading.Thread(target=server.serve_forever,daemon=True); worker.start()
        client=requests.Session(); client.trust_env=False
        url=f'http://127.0.0.1:{server.server_port}'
        try:
            for path in ('/projects','/memory','/library.mjs','/library-views.mjs','/library.css','/api/library','/api/memory-library','/api/learning-library'):
                self.assertEqual(client.get(url+path,timeout=3).status_code,200,path)
            data={'name':'HTTP fixture','description':'Scoped background'}
            self.assertEqual(client.post(url+'/api/project-save',json=data,timeout=3).status_code,403)
            headers={'X-Lodestar-Token':self.app.token,'Origin':url}
            response=client.post(url+'/api/project-save',json=data,headers=headers,timeout=3)
            self.assertEqual(response.status_code,200)
            self.assertEqual(client.get(url+'/api/project?id='+str(response.json()['id']),timeout=3).status_code,200)
            with patch('lodestar.web.server.fetch_snapshot',side_effect=AssertionError('unauthorized network')):
                self.assertEqual(client.post(url+'/api/project-import',json={'url':snapshot()['repository']},timeout=3).status_code,403)
            self.factory.assert_not_called()
        finally:
            client.close(); server.shutdown(); worker.join(5); server.server_close()


class GitHubSnapshotTests(unittest.TestCase):
    def response(self,value,status=200):
        response=Mock(status_code=status)
        response.__enter__=Mock(return_value=response); response.__exit__=Mock(return_value=False)
        response.iter_content.return_value=[json.dumps(value).encode()]
        return response

    def test_strict_urls_reject_credential_injection_hosts_and_paths(self):
        self.assertEqual(repository_url('https://github.com/a/b.git'),'https://github.com/a/b')
        for url in ('https://github.com.evil/a/b','https://token@github.com/a/b',
                    'http://github.com/a/b','https://github.com/a/b/tree/master','https://github.com/../b','https://github.com/a/b?secret=x'):
            with self.subTest(url=url),self.assertRaises(ValueError): repository_url(url)

    def test_readme_is_pinned_bounded_and_does_not_follow_returned_urls(self):
        session=Mock()
        session.get.side_effect=[self.response({'default_branch':'master'}),self.response({'sha':'a'*40}),
            self.response({'path':'README.md','type':'file','encoding':'base64','content':base64.b64encode(('x'*13000).encode()).decode(),'download_url':'https://evil.invalid'})]
        with patch('lodestar.project_workspaces.credential',return_value=None):
            result=fetch_snapshot('https://github.com/a/b',session=session)
        self.assertEqual(len(result['readme']),12000); self.assertTrue(result['truncated'])
        self.assertEqual(session.get.call_args.kwargs['params'],{'ref':'a'*40})
        self.assertTrue(all(c.args[0].startswith('https://api.github.com/repos/a/b') for c in session.get.call_args_list))
        self.assertTrue(all(c.kwargs['allow_redirects'] is False for c in session.get.call_args_list))

    def test_remote_failure_does_not_disclose_body(self):
        session=Mock(); session.get.return_value=self.response({'message':'PRIVATE_BODY_CANARY'},403)
        with patch('lodestar.project_workspaces.credential',return_value=None),self.assertRaises(RepositoryError) as caught:
            fetch_snapshot('https://github.com/a/b',session=session)
        self.assertNotIn('PRIVATE_BODY_CANARY',str(caught.exception))


if __name__=='__main__': unittest.main()
