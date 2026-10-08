import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from lodestar.agent.applicability import evaluate
from lodestar.agent.project_plan import generate
from lodestar.config import Config
from lodestar.context import Workspace
from lodestar.llm import LLMClient
from lodestar.memory import repo


class ApplicabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.cfg=Config(llm_mode='mock',model_calls_disabled=True,db_path=Path(self.temp.name)/'db',
                        workspace_dir=Path(self.temp.name)/'tasks')
        self.ws=Workspace(self.cfg)
        self.project=repo.upsert_project(self.ws.conn,'PRIVATE_PROJECT_NAME',url='https://github.com/example/repo',
                                       description='PRIVATE_DESCRIPTION',status='active')
        repo.replace_project_documents(self.ws.conn,self.project,[
            {'path':'approved.py','content':'memory evidence checkpoint with bounded source'},
            {'path':'private.py','content':'memory PRIVATE_UNAPPROVED_CODE'}])
        self.sources=[{'url':'https://arxiv.org/abs/2601.00001','content':'A memory method with independently traced evidence.',
                       'read_depth':'full','evidence_spans':[{'start':10,'end':40}]}]

    def tearDown(self):
        self.ws.close();self.temp.cleanup()

    def allow(self):
        self.cfg.project_model_allowed_repository='https://github.com/example/repo'
        self.cfg.project_model_allowed_paths=('approved.py',)

    def test_live_export_requires_repository_and_paths_before_call(self):
        llm=Mock();llm.mode='live'
        result=evaluate(self.ws,llm,'memory',self.sources,self.project)
        self.assertEqual(result['status'],'needs_export_scope')
        plan=generate(self.ws,llm,'memory',self.sources,self.project)
        self.assertEqual(plan['status'],'needs_export_scope')
        llm.complete_json.assert_not_called()
        self.allow();self.cfg.project_model_allowed_repository='https://github.com/other/repo'
        self.assertEqual(evaluate(self.ws,llm,'memory',self.sources,self.project)['status'],'needs_export_scope')
        llm.complete_json.assert_not_called()

    def test_allowed_payload_excludes_description_and_other_code(self):
        self.allow();llm=Mock();llm.mode='live'
        llm.complete_json.return_value={'decision':'uncertain','paper_method':'memory mechanism',
            'project_fit':'memory data flow','transfer_hypothesis':'unverified source tracing hypothesis',
            'project_refs':[{'path':'approved.py','quote':'memory evidence checkpoint with bounded source'}],
            'paper_refs':[{'url':self.sources[0]['url'],'quote':self.sources[0]['content']}],
            'limitations':['No experiment']}
        result=evaluate(self.ws,llm,'memory',self.sources,self.project)
        self.assertEqual(result['status'],'assessed')
        self.assertEqual(result['semantic_review'],'required')
        payload=llm.complete_json.call_args.args[2]
        for private in ('PRIVATE_PROJECT_NAME','PRIVATE_DESCRIPTION','PRIVATE_UNAPPROVED_CODE','private.py'):
            self.assertNotIn(private,payload)
        self.assertIn('evidence_spans',payload)

    def test_fabricated_quote_rejected(self):
        client=LLMClient(self.cfg)
        valid=evaluate(self.ws,client,'memory',self.sources,self.project)['assessment']
        valid['paper_refs'][0]['quote']='fabricated method with unavailable evidence'
        llm=Mock();llm.mode='mock';llm.complete_json.return_value=valid
        self.assertEqual(evaluate(self.ws,llm,'memory',self.sources,self.project)['status'],'rejected')

    def test_mock_and_model_disabled_are_explicit(self):
        result=evaluate(self.ws,LLMClient(self.cfg),'memory',self.sources,self.project)
        self.assertEqual(result['mode'],'mock')
        self.assertEqual(result['assessment']['decision'],'uncertain')
        self.allow()
        self.assertEqual(evaluate(self.ws,None,'memory',self.sources,self.project)['status'],'model_disabled')

    def test_fixture_not_sent_to_live_model(self):
        self.allow();llm=Mock();llm.mode='live'
        sources=[{**self.sources[0],'read_mode':'mock'}]
        self.assertEqual(evaluate(self.ws,llm,'memory',sources,self.project)['status'],'insufficient_evidence')
        llm.complete_json.assert_not_called()

    def test_legacy_project_mapping_does_not_export_private_metadata(self):
        from lodestar.relevance import assess_relevance
        llm=Mock();llm.mode='live'
        result=assess_relevance(self.cfg,llm,['memory improvement'],repo.list_projects(self.ws.conn))
        self.assertEqual(result['status'],'needs_metadata_export_scope')
        self.assertEqual(result['mappings'],[])
        llm.complete_json.assert_not_called()

    def test_technical_identifier_in_chinese_goal_matches_approved_context_only(self):
        from lodestar.agent.project_evidence import collect
        self.allow()
        repo.replace_project_documents(self.ws.conn,self.project,[
            {'path':'approved.py','content':'temporary context state with original source evidence'},
            {'path':'private.py','content':'context PRIVATE_UNAPPROVED_CODE'}])
        other = repo.upsert_project(self.ws.conn,'other project')
        repo.replace_project_documents(self.ws.conn,other,[{'path':'approved.py','content':'context OTHER_PROJECT_SECRET'}])
        result = collect(self.ws,'请评估 context-as-a-file 方法的临时上下文',self.sources,self.project,live=True)
        self.assertEqual(result['status'],'ready')
        self.assertEqual([doc['path'] for doc in result['documents']],['approved.py'])
        self.assertNotIn('PRIVATE_UNAPPROVED_CODE',json.dumps(result))
        self.assertNotIn('OTHER_PROJECT_SECRET',json.dumps(result))

    def test_identifier_token_expansion_does_not_select_unmatched_project_documents(self):
        matches = repo.search_project_documents(self.ws.conn,'请评估 novel-mechanism',project_id=self.project)
        self.assertEqual(matches,[])
