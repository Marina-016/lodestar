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

    def _assessed_candidate(self):
        from dataclasses import replace
        from lodestar.llm import LLMClient
        repo.replace_project_documents(self.ws.conn,self.project,[
            {'path':'core.py','content':'memory checkpoint store with traceable bounded evidence'}])
        candidate.read(self.ws,self.project,self.id,reader=self.reader)
        assessment=candidate.assess(self.ws,LLMClient(replace(self.config,llm_mode='mock')),
                                    self.project,self.id,'memory')
        self.assertEqual(assessment['status'],'assessed')
        return assessment

    def _plan_client(self):
        paper=candidate.evidence(self.ws,self.project,self.id)['evidence']
        llm=Mock();llm.mode='mock'
        llm.complete_json.return_value={
            'problem':{'description':'Bound memory evidence', 'project_refs':[{
                'path':'core.py','quote':'memory checkpoint store with traceable bounded evidence'}]},
            'method':{'description':'Unverified paper transfer hypothesis','paper_refs':[{
                'url':paper['url'],'quote':paper['content'][:80]}]},
            'changes':[{'path':'core.py','description':'Preserve source offsets'}],
            'experiment':{'hypothesis':'Better traceability','baseline':'current','candidate':'offsets',
                          'metrics':['manual source location accuracy'],'constraints':['fixed tasks']},
            'risks':['no demonstrated benefit'],'missing_evidence':['No A/B executed']}
        return llm

    def test_plan_uses_assessed_snapshot_and_persists_lineage(self):
        assessment=self._assessed_candidate();llm=self._plan_client()
        result=candidate.plan(self.ws,llm,self.project,self.id,'new wording',assessment['assessment_id'])
        self.assertTrue(result['contract_valid'])
        self.assertEqual(result['assessment_id'],assessment['assessment_id'])
        self.assertEqual(result['read_id'],assessment['read_id'])
        self.assertEqual(result['execution_status'],'not_run')
        payload=json.loads(llm.complete_json.call_args.args[2])
        self.assertEqual(payload['documents'],assessment['evidence_snapshot']['documents'])
        self.assertEqual(payload['unreviewed_applicability']['decision'],'uncertain')
        self.assertEqual(candidate.plans(self.ws,self.project,self.id)[0]['id'],result['plan_id'])

    def test_new_read_or_changed_project_requires_reassessment(self):
        self._assessed_candidate();llm=self._plan_client()
        candidate.read(self.ws,self.project,self.id,refresh=True,reader=self.reader)
        self.assertEqual(candidate.plan(self.ws,llm,self.project,self.id,'memory')['status'],'needs_reassessment')
        llm.complete_json.assert_not_called()
        self._assessed_candidate()
        repo.replace_project_documents(self.ws.conn,self.project,[{'path':'core.py','content':'changed memory code'}])
        self.assertEqual(candidate.plan(self.ws,llm,self.project,self.id,'memory')['status'],'needs_reassessment')
        llm.complete_json.assert_not_called()

    def test_plan_requires_assessment_and_does_not_promote_non_applicable(self):
        candidate.read(self.ws,self.project,self.id,reader=self.reader)
        llm=self._plan_client()
        self.assertEqual(candidate.plan(self.ws,llm,self.project,self.id,'memory')['status'],'needs_assessment')
        assessment=self._assessed_candidate()
        stored={k:v for k,v in assessment.items() if k not in ('assessment_id','read_id')}
        stored['assessment']['decision']='not_applicable'
        self.ws.conn.execute('UPDATE paper_candidate_assessments SET result=? WHERE id=?',
                             (json.dumps(stored),assessment['assessment_id']))
        self.ws.conn.commit()
        self.assertEqual(candidate.plan(self.ws,llm,self.project,self.id,'memory')['status'],'not_applicable')
        llm.complete_json.assert_not_called()

    def test_mock_assessment_cannot_feed_live_plan(self):
        self._assessed_candidate()
        repo.upsert_project(self.ws.conn,'Project',url='https://github.com/example/project',status='active')
        self.config.project_model_allowed_repository='https://github.com/example/project'
        self.config.project_model_allowed_paths=('core.py',)
        llm=self._plan_client();llm.mode='live'
        self.assertEqual(candidate.plan(self.ws,llm,self.project,self.id,'memory')['status'],'needs_live_assessment')
        llm.complete_json.assert_not_called()

    def test_experiment_links_measured_outputs_to_plan_without_adoption(self):
        from lodestar.agent import candidate_experiment
        self._assessed_candidate()
        plan=candidate.plan(self.ws,self._plan_client(),self.project,self.id,'memory')
        root=Path(self.temp.name)
        protocol=root/'protocol.json'
        protocol.write_text(json.dumps({'version':1,'hypothesis':'fixed exact-output diagnostic',
            'dataset_kind':'functional_fixture','grader':'exact_output',
            'cases':[{'id':'one','input':1,'expected':2}]}),encoding='utf-8')
        baseline=root/'baseline.py';other=root/'other.py'
        baseline.write_text("import json;print(json.dumps({'status':'complete','results':[{'case_id':'one','output':1}]}))",encoding='utf-8')
        other.write_text("import json;print(json.dumps({'status':'complete','results':[{'case_id':'one','output':2}]}))",encoding='utf-8')
        result=candidate_experiment.execute(self.ws,self.project,self.id,plan['plan_id'],protocol,baseline,other,root/'ab')
        self.assertEqual(result['verdict'],'measured')
        self.assertEqual(result['adoption_status'],'not_decided')
        self.assertEqual(result['lineage']['plan_id'],plan['plan_id'])
        self.assertEqual(result['lineage']['read_id'],plan['read_id'])
        self.assertEqual(candidate_experiment.history(self.ws,self.project,self.id)[0]['id'],result['experiment_id'])
        stored=json.loads((root/'ab/result.json').read_text(encoding='utf-8'))
        self.assertEqual(stored['lineage'],result['lineage'])
        with self.assertRaises(ValueError):
            candidate_experiment.execute(self.ws,self.project,self.id,999,protocol,baseline,other,root/'invalid')
        self.assertFalse((root/'invalid').exists())

    def test_changed_paper_version_does_not_reuse_old_read(self):
        old=candidate.read(self.ws,self.project,self.id,reader=self.reader)
        row=self.ws.conn.execute('SELECT source FROM paper_recommendations WHERE id=?',(self.id,)).fetchone()
        source=json.loads(row['source'])
        source['url']='https://arxiv.org/abs/2601.00001v2'
        self.ws.conn.execute('UPDATE paper_recommendations SET source=? WHERE id=?',(json.dumps(source),self.id))
        self.ws.conn.commit()
        self.assertIsNone(candidate.evidence(self.ws,self.project,self.id))
        failed=candidate.read(self.ws,self.project,self.id,reader=Mock(return_value={'error':'timeout'}))
        self.assertEqual(failed['status'],'error')
        self.assertIsNone(candidate.evidence(self.ws,self.project,self.id))
        new=candidate.read(self.ws,self.project,self.id,reader=self.reader)
        self.assertFalse(new['cached'])
        self.assertNotEqual(new['read_id'],old['read_id'])
        self.assertEqual(self.reader.call_args.args[1],source['url'])
        self.assertEqual(self.ws.conn.execute('SELECT count(*) FROM paper_candidate_reads').fetchone()[0],3)

    def test_no_change_plan_cannot_start_implementation_experiment(self):
        from lodestar.agent import candidate_experiment
        self._assessed_candidate()
        plan=candidate.plan(self.ws,self._plan_client(),self.project,self.id,'memory')
        row=self.ws.conn.execute('SELECT result FROM paper_candidate_plans WHERE id=?',(plan['plan_id'],)).fetchone()
        data=json.loads(row['result']);data['action']='no_change'
        self.ws.conn.execute('UPDATE paper_candidate_plans SET result=? WHERE id=?',(json.dumps(data),plan['plan_id']))
        self.ws.conn.commit()
        out=Path(self.temp.name)/'must-not-run'
        with self.assertRaisesRegex(ValueError,'Investigation/no-change'):
            candidate_experiment.execute(self.ws,self.project,self.id,plan['plan_id'],'missing.json','missing.py','missing.py',out)
        self.assertFalse(out.exists())
        self.assertEqual(candidate_experiment.history(self.ws,self.project,self.id),[])
