import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from lodestar.agent import watch
from lodestar.config import Config
from lodestar.context import Workspace
from lodestar.memory import repo
from lodestar.memory import watch as store


class WatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = Config(db_path=Path(self.temp.name)/'test.db', workspace_dir=Path(self.temp.name)/'tasks',
                             model_calls_disabled=True)
        self.ws = Workspace(self.config)
        self.project = repo.upsert_project(self.ws.conn, 'Test', status='active')
        self.now = datetime(2026,10,8,tzinfo=timezone.utc)
        store.subscribe(self.ws.conn,self.project,'agent memory',['memory'],24,watch._iso(self.now))

    def tearDown(self):
        self.ws.close()
        self.temp.cleanup()

    def discover(self, ws, query, *, kind, **kwargs):
        return {'status':'ok','kind':kind,'mode':'test','sources':[
            {'title':'Agent Memory', 'snippet':'A memory retrieval method',
             'url':'https://arxiv.org/abs/2601.00001'+('v2' if kind=='recent' else '')},
            {'title':'Unrelated physics','url':'https://arxiv.org/abs/2601.00002'},
            {'title':'Memory advertisement','url':'https://example.com/abs/fake'}]}

    def test_version_dedup_channels_and_no_model_or_mastery(self):
        with patch('lodestar.llm.LLMClient',side_effect=AssertionError('model forbidden')):
            first=watch.tick(self.ws,now=self.now,discovery=self.discover)
            self.assertEqual(first['runs'][0]['new_recommendations'],1)
            second=watch.tick(self.ws,now=self.now+timedelta(hours=24),discovery=self.discover)
            self.assertEqual(second['runs'][0]['new_recommendations'],0)
        inbox=store.inbox(self.ws.conn,self.project)
        self.assertEqual(len(inbox),1)
        self.assertEqual(inbox[0]['channels'],['recent','trending'])
        self.assertIn(inbox[0]['matches'][0]['quote'],inbox[0]['source']['title'])
        self.assertEqual(self.ws.conn.execute('SELECT count(*) FROM learning_events').fetchone()[0],0)

    def test_due_restart_disable_and_inactive_project(self):
        watch.tick(self.ws,now=self.now,discovery=self.discover)
        self.ws.close(); self.ws=Workspace(self.config)
        self.assertEqual(watch.tick(self.ws,now=self.now,discovery=self.discover)['runs'],[])
        repo.set_project_status(self.ws.conn,self.project,'paused')
        self.assertEqual(watch.tick(self.ws,now=self.now+timedelta(days=2),discovery=self.discover)['runs'],[])
        repo.set_project_status(self.ws.conn,self.project,'active')
        self.ws.conn.execute('UPDATE paper_watches SET enabled=0');self.ws.conn.commit()
        self.assertEqual(watch.tick(self.ws,now=self.now+timedelta(days=2),discovery=self.discover)['runs'],[])

    def test_failure_is_not_empty_success_and_retries_soon(self):
        def fail(*args, **kwargs):
            return {'status':'error','sources':[],'error':'provider unavailable'}
        result=watch.tick(self.ws,now=self.now,discovery=fail)['runs'][0]
        self.assertEqual(result['status'],'error')
        next_run=self.ws.conn.execute('SELECT next_run FROM paper_watches').fetchone()[0]
        self.assertEqual(next_run,watch._iso(self.now+timedelta(hours=1)))
        self.assertEqual(store.inbox(self.ws.conn,self.project),[])

    def test_partial_result_keeps_successful_evidence(self):
        def partial(ws,query,*,kind,**kwargs):
            return self.discover(ws,query,kind=kind) if kind=='recent' else {'status':'error','sources':[]}
        result=watch.tick(self.ws,now=self.now,discovery=partial)['runs'][0]
        self.assertEqual(result['status'],'partial')
        self.assertEqual(store.inbox(self.ws.conn,self.project)[0]['channels'],['recent'])

    def test_live_lease_prevents_parallel_work_and_expired_lease_recovers(self):
        self.ws.conn.execute('UPDATE paper_watches SET lease_until=?',(watch._iso(self.now+timedelta(minutes=10)),))
        self.ws.conn.commit()
        self.assertEqual(watch.tick(self.ws,now=self.now,discovery=self.discover)['runs'],[])
        self.assertEqual(len(watch.tick(self.ws,now=self.now+timedelta(minutes=11),discovery=self.discover)['runs']),1)

    def test_public_query_only_no_project_description_export(self):
        repo.upsert_project(self.ws.conn,'Test',description='PRIVATE_PROJECT_CONTEXT',status='active')
        calls=[]
        def capture(ws,query,**kwargs):
            calls.append(query)
            return {'status':'ok','sources':[]}
        watch.tick(self.ws,now=self.now,discovery=capture)
        self.assertEqual(calls,['agent memory','agent memory'])

    def test_invalid_subscription_and_false_substring(self):
        with self.assertRaises(ValueError):
            watch.subscribe(self.ws,999,'agent',['agent'])
        with self.assertRaises(ValueError):
            watch.subscribe(self.ws,self.project,'agent',[],24)
        self.assertEqual(watch.match_terms({'title':'Agentic architecture'},['agent']),[])

    def test_fixture_provenance_does_not_overwrite_live_paper(self):
        watch.tick(self.ws,now=self.now,discovery=self.discover)
        def fixture(ws,query,**kwargs):
            result=self.discover(ws,query,**kwargs)
            result['mode']='mock'
            return result
        watch.tick(self.ws,now=self.now+timedelta(days=1),discovery=fixture)
        inbox=store.inbox(self.ws.conn,self.project)
        self.assertEqual(len(inbox),2)
        self.assertEqual({r['source']['discovery_mode'] for r in inbox},{'test','mock'})
        self.assertEqual(sum(r['paper_key'].startswith('fixture:') for r in inbox),1)

    def test_new_version_resets_read_state_and_old_version_cannot_downgrade(self):
        version = [1]
        def discover(ws,query,*,kind,**kwargs):
            return {'status':'ok','sources':[{'title':'Agent memory','url':'https://arxiv.org/abs/2601.00001v'+str(version[0])}]}
        watch.tick(self.ws,now=self.now,discovery=discover)
        self.ws.conn.execute("UPDATE paper_recommendations SET state='read'");self.ws.conn.commit()
        version[0]=2
        watch.tick(self.ws,now=self.now+timedelta(days=1),discovery=discover)
        row=store.inbox(self.ws.conn,self.project)[0]
        self.assertEqual(row['state'],'unread')
        self.assertTrue(row['source']['url'].endswith('v2'))
        self.ws.conn.execute("UPDATE paper_recommendations SET state='read'");self.ws.conn.commit()
        version[0]=1
        watch.tick(self.ws,now=self.now+timedelta(days=2),discovery=discover)
        rows=store.inbox(self.ws.conn,self.project)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['state'],'read')
        self.assertTrue(rows[0]['source']['url'].endswith('v2'))
