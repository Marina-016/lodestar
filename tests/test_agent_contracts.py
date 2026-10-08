import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

from lodestar.agent.loop import ResearchAgent
from lodestar.agent.project_plan import generate
from lodestar.config import Config
from lodestar.context import Workspace
from lodestar.memory import repo
from lodestar.tools.discovery import discover
from lodestar.tools.arxiv_search import _search_arxiv


class AgentContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.ws = Workspace(Config(llm_mode='mock', search_mode='mock',
            db_path=Path(self.temp.name) / 'test.db', workspace_dir=Path(self.temp.name) / 'ws'))

    def tearDown(self):
        self.ws.close()
        self.temp.cleanup()

    def test_unattended_research_keeps_updates_pending(self):
        result = ResearchAgent(self.ws, interactive=False).run('agent memory research', discovery_days=7)
        self.assertNotIn('error', result)
        self.assertTrue(result['updates'])
        self.assertTrue(all(u['status'] == 'pending' for u in result['updates']))
        self.assertEqual(repo.list_concepts(self.ws.conn), [])
        self.assertEqual(self.ws.conn.execute('SELECT COUNT(*) FROM learning_events').fetchone()[0], 0)

    def test_source_failure_is_not_empty_success(self):
        self.ws.config.search_mode = 'live'
        with patch('lodestar.tools.discovery.requests.get', side_effect=requests.Timeout('timeout')):
            result = discover(self.ws, 'agent', kind='trending')
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['sources'], [])

    def test_arxiv_versions_deduplicate_and_dates_survive(self):
        from datetime import datetime, timezone
        response = unittest.mock.Mock()
        response.text = '''<feed xmlns="http://www.w3.org/2005/Atom"><entry>
        <id>http://arxiv.org/abs/2601.00001v2</id><title>Agent</title>
        <published>2026-01-01T00:00:00Z</published><updated>2026-01-03T00:00:00Z</updated>
        <summary>Method</summary></entry></feed>'''
        with patch('lodestar.tools.arxiv_search.requests.get', return_value=response) as request:
            sources = _search_arxiv('agent', sort_by='submittedDate',
                since=datetime(2026, 1, 1, tzinfo=timezone.utc),
                until=datetime(2026, 1, 5, tzinfo=timezone.utc))
        self.assertEqual(sources[0]['dedup_key'], 'arxiv:2601.00001')
        self.assertEqual(sources[0]['version'], '2601.00001v2')
        self.assertIn('submittedDate:[202601010000 TO 202601050000]', request.call_args.kwargs['params']['search_query'])

    def test_project_plan_requires_evidence_and_never_executes(self):
        project_id = repo.upsert_project(self.ws.conn, 'test', description='Memory agent', status='active')
        llm = unittest.mock.Mock()
        result = generate(self.ws, llm, 'memory', [], project_id)
        self.assertEqual(result['status'], 'draft')
        self.assertTrue(result['missing_evidence'])
        llm.complete.assert_not_called()

    def test_project_plan_reads_registered_document(self):
        project_id = repo.upsert_project(self.ws.conn, 'test', description='Memory agent', status='active')
        repo.replace_project_documents(self.ws.conn, project_id,
            [{'path': 'memory.py', 'title': 'memory', 'content': 'memory checkpoint example source', 'source': 'local'}])
        llm = unittest.mock.Mock()
        llm.complete_json.return_value = {
            'problem': {'description': 'Memory change', 'project_refs': [{'path':'memory.py','quote':'memory checkpoint example source'}]},
            'method': {'description': 'Paper mechanism hypothesis', 'paper_refs': [{'url':'https://arxiv.org/abs/2601.00001','quote':'paper method with bounded evidence'}]},
            'changes':[{'path':'memory.py','description':'Bound recall'}],
            'experiment':{'hypothesis':'Reduced context','baseline':'all','candidate':'bounded','metrics':['coverage'],'constraints':['fixed tasks']},
            'risks':['coverage loss'],'missing_evidence':['needs experiment']}
        result = generate(self.ws, llm, 'memory',
            [{'url': 'https://arxiv.org/abs/2601.00001', 'content': 'paper method with bounded evidence', 'read_depth': 'full'}], project_id)
        self.assertTrue(result['contract_valid'])
        self.assertEqual(result['execution_status'], 'not_run')
        self.assertIn('memory checkpoint', llm.complete_json.call_args.args[2])

    def test_brief_does_not_confuse_research_cache_with_mastery(self):
        repo.upsert_concept(self.ws.conn, 'Harness', notes=['Research cache entry'])
        result = ResearchAgent(self.ws, interactive=False).run('agent memory research', discovery_days=7)
        brief = result['brief_md']
        self.assertNotIn('你已掌握', brief)
        self.assertNotIn('进入 Knowledge State', brief)
        self.assertNotIn('经验与 Trace 收集 →', brief)
        self.assertIn('研究笔记写入状态', brief)
        self.assertIn('待确认', brief)

    def test_trending_does_not_claim_submission_time_filter(self):
        self.ws.config.search_mode = 'live'
        response = unittest.mock.Mock()
        response.json.return_value = [{'paper': {'id': '2401.00001', 'title': 'Agent Harness',
                                    'summary': 'Agent research', 'publishedAt': '2024-01-01'}}]
        with patch('lodestar.tools.discovery.requests.get', return_value=response):
            result = discover(self.ws, 'Agent', kind='trending')
        self.assertNotIn('from_utc', result)
        self.assertEqual(result['sources'][0]['date'], '2024-01-01')
        self.assertEqual(result['sources'][0]['discovery_kind'], 'trending')

    def test_discovery_reads_include_new_and_trending_without_weak_promotion(self):
        hot = {'url': 'hot', 'score': 9, 'discovery_kind': 'trending'}
        recent = {'url': 'new', 'score': 7, 'discovery_kind': 'recent'}
        weak = {'url': 'weak', 'score': 2, 'discovery_kind': 'recent'}
        ordered = ResearchAgent._balance_discovery_reads([hot, recent, weak], 5)
        self.assertEqual([s['url'] for s in ordered[:2]], ['new', 'hot'])
        ordered = ResearchAgent._balance_discovery_reads([hot, weak], 5)
        self.assertEqual(ordered[0]['url'], 'hot')
