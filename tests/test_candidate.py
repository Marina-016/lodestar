import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from lodestar.agent import candidate
from lodestar.agent.conversation import ConversationAgent
from lodestar.config import Config
from lodestar.context import Workspace
from lodestar.memory import repo


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.config=Config(db_path=Path(self.temp.name)/'db',workspace_dir=Path(self.temp.name)/'tasks',
                           model_calls_disabled=True,read_char_budget=500)
        self.ws=Workspace(self.config)
        self.project=repo.upsert_project(self.ws.conn,'Project',status='active')
        source={'title':'Public Paper','url':'https://arxiv.org/abs/2601.00001','source_type':'paper'}
        cursor=self.ws.conn.execute("""INSERT INTO paper_recommendations(project_id,paper_key,source,matches,first_seen,last_seen,channels)
            VALUES(?,?,?,?,?,?,?)""",(self.project,'arxiv:2601.00001',json.dumps(source),'[]','now','now','[]'))
        self.id=cursor.lastrowid;self.ws.conn.commit()
        self.reader=Mock(return_value={'text':'Paper method '+('x'*600),'read_depth':'full','coverage':'body excerpts',
                                      'evidence_spans':[{'start':100,'end':200}],'note':'partial reading'})

    def tearDown(self):
        self.ws.close();self.temp.cleanup()

    def test_read_bound_metadata_cache_and_no_mastery(self):
        result=candidate.read(self.ws,self.project,self.id,reader=self.reader)
        self.assertEqual(result['status'],'read')
        self.assertEqual(len(result['evidence']['content']),500)
        self.assertTrue(result['evidence']['truncated'])
        self.assertEqual(result['evidence']['evidence_spans'],[{'start':100,'end':200}])
        self.assertTrue(candidate.read(self.ws,self.project,self.id,reader=self.reader)['cached'])
        self.assertEqual(self.reader.call_count,1)
        self.assertEqual(self.reader.call_args.kwargs['query'],'Public Paper')
        self.assertEqual(self.ws.conn.execute('SELECT count(*) FROM learning_events').fetchone()[0],0)

    def test_failed_refresh_retains_good_evidence_and_attempt(self):
        good=candidate.read(self.ws,self.project,self.id,reader=self.reader)
        result=candidate.read(self.ws,self.project,self.id,refresh=True,reader=Mock(return_value={'error':'timeout'}))
        self.assertEqual(result['status'],'error')
        self.assertTrue(result['previous_evidence_retained'])
        self.assertEqual(candidate.evidence(self.ws,self.project,self.id)['id'],good['read_id'])
        self.assertEqual(self.ws.conn.execute('SELECT count(*) FROM paper_candidate_reads').fetchone()[0],2)

    def test_wrong_project_and_fixture_mode_block_before_reader(self):
        with self.assertRaises(ValueError):
            candidate.read(self.ws,999,self.id,reader=self.reader)
        self.config.search_mode='mock'
        with self.assertRaises(ValueError):
            candidate.read(self.ws,self.project,self.id,reader=self.reader)
        self.reader.assert_not_called()

    def test_handoff_persists_bound_project_and_evidence_after_restart(self):
        candidate.read(self.ws,self.project,self.id,reader=self.reader)
        result=candidate.handoff(self.ws,self.project,self.id,'alice')
        self.ws.close();self.ws=Workspace(self.config)
        agent=ConversationAgent(self.ws,None)
        session=agent._session(result['conversation_id'],'alice')
        self.assertEqual(session['project_id'],self.project)
        self.assertEqual(json.loads(session['evidence'])[0]['read_depth'],'full')
        self.assertEqual(agent.history(result['conversation_id'],'alice')[0]['kind'],'candidate_evidence')
        with self.assertRaises(ValueError):
            agent.history(result['conversation_id'],'bob')

    def test_failed_read_cannot_handoff(self):
        result=candidate.read(self.ws,self.project,self.id,reader=Mock(return_value={'text':''}))
        self.assertEqual(result['status'],'error')
        with self.assertRaises(ValueError):
            candidate.handoff(self.ws,self.project,self.id)
        self.assertEqual(self.ws.conn.execute('SELECT count(*) FROM agent_sessions').fetchone()[0],0)

    def test_abstract_is_not_upgraded_to_full(self):
        result=candidate.read(self.ws,self.project,self.id,reader=Mock(return_value={'text':'abstract only','read_depth':'abstract'}))
        self.assertEqual(result['evidence']['read_depth'],'abstract')
        self.assertEqual(result['evidence']['semantic_applicability'],'pending')

    def test_abstract_refresh_does_not_discard_previous_body_evidence(self):
        original=candidate.read(self.ws,self.project,self.id,reader=self.reader)
        refreshed=candidate.read(self.ws,self.project,self.id,refresh=True,
                                 reader=Mock(return_value={'text':'fallback abstract','read_depth':'abstract'}))
        self.assertEqual(refreshed['evidence']['read_depth'],'abstract')
        self.assertEqual(candidate.evidence(self.ws,self.project,self.id)['id'],original['read_id'])
