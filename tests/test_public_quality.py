"""Frozen public-source replay tests use scripted evidence, never a live model."""
import contextlib
from copy import deepcopy
from hashlib import sha256
import io
import json
from pathlib import Path
import socket
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from lodestar.config import Config
from lodestar.llm import LLMClient
from lodestar.tools import registry
from scripts.eval_workspace import observed_coverage, run
from scripts.public_paper_fixtures import PublicPaperFixture, freeze
from scripts import quality_fixtures


def packet():
    papers = []
    for i, name in enumerate(('Alpha', 'Beta'), 1):
        text = 'Method\nBODY_ONLY_CANARY_' + name + ' memory mechanism.\nExperiments\nMeasured memory results.'
        source = {'url': f'https://arxiv.org/abs/2601.0000{i}v1', 'title': name+' Memory Study',
                  'abstract': name+' memory abstract.', 'authors': ['Test Author'], 'date': '2024-01-01',
                  'updated_at': '2025-01-01', 'retrieved_at': '2026-10-18T01:00:00+00:00'}
        papers.append({'source': source, 'text': text, 'text_sha256': sha256(text.encode()).hexdigest(),
                       'pdf_sha256': 'a'*64})
    return {'schema_version': 1, 'synthetic': False, 'frozen_at': '2026-10-18T12:00:00+08:00', 'papers': papers}


class PublicScriptedModel(LLMClient):
    observed = []

    def __init__(self, config):
        super().__init__(config)
        self.steps = 0

    def dialogue_step(self, system, messages, tools, on_text=None):
        self.observed.append(json.dumps(messages, ensure_ascii=False))
        request = next((m['content'] for m in reversed(messages)
                        if m['role'] == 'user' and isinstance(m['content'], str)), '')
        actions = []
        if self.steps == 0:
            if '请找出这些历史论文' in request:
                actions = [('search_papers', {'queries': ['Alpha', 'Beta'], 'days': 0})]
            elif '比较刚才这些论文' in request:
                actions = [('read_paper', {'url': p['source']['url'], 'full_text': True, 'query': 'memory'})
                           for p in packet()['papers']]
            elif '总费用更低' in request:
                actions = [('verify_paper', {'url': p['source']['url']}) for p in packet()['papers']]
        self.steps += 1
        if actions:
            return {'role': 'assistant', 'content': [
                {'type': 'tool_use', 'id': f'public-test-{i}', 'name': name, 'input': args}
                for i, (name, args) in enumerate(actions)]}
        text = 'Scripted transport response; no semantic acceptance.'
        if on_text:
            on_text(text)
        return {'role': 'assistant', 'content': [{'type': 'text', 'text': text}]}


class PublicQualityTests(unittest.TestCase):
    def setUp(self):
        self.fixture = PublicPaperFixture(packet())
        self.ws = SimpleNamespace(config=Config())

    def test_search_is_abstract_only_and_respects_query_and_first_submission_window(self):
        result = self.fixture.call_tool(self.ws, 'search_papers', {'query': 'Alpha', 'days': 0})
        self.assertEqual(len(result['sources']), 1)
        self.assertIn('Alpha', result['sources'][0]['title'])
        self.assertNotIn('BODY_ONLY_CANARY', json.dumps(result))
        self.assertNotIn('text_sha256', json.dumps(result))
        self.assertEqual(self.fixture.call_tool(self.ws, 'search_papers', {'query': 'unrelated'})['sources'], [])
        self.assertEqual(self.fixture.call_tool(self.ws, 'search_papers', {'query': 'Alpha', 'days': 7})['sources'], [])
        self.assertEqual(self.fixture.call_tool(self.ws, 'search_papers',
            {'query': 'Alpha', 'sort_by': 'submittedDate'})['sources'], [])

    def test_body_requires_read_and_preserves_offsets(self):
        paper = packet()['papers'][0]
        abstract = self.fixture.call_tool(self.ws, 'read_paper', {'url': paper['source']['url']})
        self.assertEqual(abstract['read_depth'], 'abstract')
        self.assertNotIn('BODY_ONLY_CANARY', abstract['text'])
        body = self.fixture.call_tool(self.ws, 'read_paper', {'url': paper['source']['url'],
            'full_text': True, 'query': 'memory', 'char_budget': 1000})
        self.assertEqual(body['read_depth'], 'full')
        self.assertIn('BODY_ONLY_CANARY', body['text'])
        self.assertTrue(body['query_matched'])
        self.assertEqual('\n\n'.join(paper['text'][s['start']:s['end']] for s in body['evidence_spans']), body['text'])

    def test_external_or_unknown_tools_never_fall_back_to_network(self):
        with patch('requests.get', side_effect=AssertionError('No network')):
            for name, params in [('search_web', {'query': 'Alpha'}), ('discover_papers', {}),
                    ('read_paper', {'url': 'https://example.test/private'}),
                    ('verify_paper', {'url': 'https://example.test/private'})]:
                self.assertIn('error', self.fixture.call_tool(self.ws, name, params))
            lookup = self.fixture.verify_publication(self.ws, self.fixture.candidates()[0])
            self.assertEqual(lookup['publication_status'], 'not_checked')
            self.assertNotIn('verified_at', lookup)

    def test_snapshot_hash_and_review_separation_are_validated(self):
        for change in ('hash', 'version', 'duplicate', 'annotation', 'naive_time'):
            value = packet()
            if change == 'hash':
                value['papers'][0]['text'] += 'tampered'
            elif change == 'version':
                value['papers'][0]['source']['url'] = 'https://arxiv.org/abs/2601.00001'
            elif change == 'duplicate':
                value['papers'][1] = deepcopy(value['papers'][0])
            elif change == 'annotation':
                value['papers'][0]['source']['human_answer'] = 'REVIEW_ONLY_CANARY'
            else:
                value['frozen_at'] = '2026-10-18T12:00:00'
            with self.subTest(change=change), self.assertRaises(ValueError):
                PublicPaperFixture(value)
        source = self.fixture.snapshot()
        source['papers'][0]['text'] = 'mutated'
        self.assertNotEqual(self.fixture.snapshot()['papers'][0]['text'], 'mutated')

    def test_public_quality_http_is_isolated_and_keeps_semantics_unscored(self):
        original = registry.call_tool
        connect = socket.socket.connect
        def local_only(sock, address):
            if address[0] not in ('127.0.0.1', '::1'):
                raise AssertionError('No external connections')
            return connect(sock, address)
        PublicScriptedModel.observed = []
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root/'source.json'
            path.write_text(json.dumps(packet()), encoding='utf-8')
            user_db = root/'user.db'
            user_db.write_bytes(b'USER_DB_UNCHANGED')
            with contextlib.redirect_stdout(io.StringIO()), patch('socket.socket.connect', local_only):
                report = run(Config(llm_mode='mock', search_mode='mock', db_path=user_db), root/'run',
                    scenario='public-quality', source_packet=path, llm_factory=PublicScriptedModel)
            self.assertEqual(user_db.read_bytes(), b'USER_DB_UNCHANGED')
            self.assertTrue(all(report['checks'].values()), report)
            self.assertFalse(report['source_fixture']['synthetic'])
            self.assertEqual(report['semantic_review'], 'not_applicable_offline_fixture')
            self.assertEqual(report['model_attempts'], 0)
            self.assertEqual(report['remaining_runs_at_exit'], 0)
            self.assertEqual(len(report['turns']), 4)
            urls = {p['source']['url'] for p in packet()['papers']}
            self.assertEqual(set(report['observed_coverage']['body_reads']), urls)
            self.assertEqual(report['observed_coverage']['publication_lookups'], [])
            observed = '\n'.join(PublicScriptedModel.observed)
            self.assertIn('BODY_ONLY_CANARY', observed)
            self.assertNotIn('text_sha256', observed)
            self.assertNotIn('review_only', observed)
            frozen = (root/'run/source-snapshot.json').read_bytes()
            self.assertEqual(sha256(frozen).hexdigest(), report['source_fixture']['sha256'])
            self.assertEqual(json.loads(frozen), packet())
            self.assertTrue(json.loads((root/'run/human-facts.json').read_text())['review_only'])
            self.assertIs(registry.call_tool, original)

    def test_invalid_scenario_options_fail_before_creating_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root/'source.json'
            path.write_text(json.dumps(packet()), encoding='utf-8')
            for scenario, source, mode in [('public-quality', None, 'mock'),
                    ('quality', path, 'mock'), ('public-quality', path, 'live')]:
                with self.subTest(scenario=scenario, mode=mode), self.assertRaises(ValueError):
                    run(Config(llm_mode='mock', search_mode=mode), root/'run', scenario=scenario, source_packet=source)
                self.assertFalse((root/'run').exists())

    def test_freeze_rejects_metadata_path_escape_without_creating_packet(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            status = {'synthetic': False, 'papers': [{'url': 'https://arxiv.org/abs/2601.00001v1',
                                                     'official_metadata_file': '../outside.json'}]}
            (root/'collection-status.json').write_text(json.dumps(status), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'outside collection'):
                freeze(root, root/'out.json')
            self.assertFalse((root/'out.json').exists())

    def test_coverage_uses_outcomes_and_excludes_fallback_unmatched_empty_and_duplicate(self):
        base = {'action': 'read_paper', 'params': {'url': 'paper', 'full_text': True},
                'executed': True, 'error': None, 'read_depth': 'full', 'returned_text_chars': 100}
        for changes, expected in [({}, ['paper']), ({'read_depth': 'abstract'}, []),
                ({'query_matched': False}, []), ({'returned_text_chars': 0}, []),
                ({'error': 'failed'}, []), ({'executed': False}, [])]:
            with self.subTest(changes=changes):
                coverage = observed_coverage([{'actions': [{**base, **changes}]}])
                self.assertEqual(coverage['body_reads'], expected)
                self.assertEqual(coverage['abstract_fallbacks'], ['paper'] if changes.get('read_depth') == 'abstract' else [])

    def test_synthetic_http_full_text_fallback_is_not_reported_as_body_evidence(self):
        from test_quality_acceptance import FixtureModel
        original_read = quality_fixtures.read_paper
        def abstract_only(url, **options):
            options['full_text'] = False
            return original_read(url, **options)
        with tempfile.TemporaryDirectory() as directory:
            with contextlib.redirect_stdout(io.StringIO()), patch(
                    'scripts.quality_fixtures.read_paper', side_effect=abstract_only):
                report = run(Config(llm_mode='mock', search_mode='mock'), Path(directory)/'run',
                             scenario='quality', llm_factory=FixtureModel)
            self.assertTrue(all(report['checks'].values()), report)
            self.assertEqual(len(report['observed_coverage']['body_read_requests']), 2)
            self.assertEqual(len(report['observed_coverage']['abstract_fallbacks']), 2)
            self.assertEqual(report['observed_coverage']['body_reads'], [])
