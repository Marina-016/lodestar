"""Fixture contracts are not a semantic quality score."""
import json
from datetime import datetime
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from scripts import quality_fixtures as fixtures


class QualityFixtureTests(unittest.TestCase):
    def setUp(self):
        self.ws = SimpleNamespace(config=SimpleNamespace(
            read_char_budget=12000, arxiv_results_per_query=6, search_mode='live'))
        self.sources = {s['fixture_key']: s for s in fixtures.candidates()}

    def test_source_identity_is_synthetic_frozen_and_separate_from_review_answers(self):
        self.assertEqual(set(self.sources), {'A', 'B', 'C', 'D'})
        for source in self.sources.values():
            self.assertTrue(source['synthetic'])
            self.assertEqual(urlsplit(source['url']).hostname, 'example.invalid')
            self.assertEqual(source['date_basis'], 'first_submission')
            self.assertIsNotNone(datetime.fromisoformat(source['retrieved_at']).tzinfo)
        self.assertLess(self.sources['C']['published_at'], self.sources['C']['updated_at'])
        response = fixtures.call_tool(self.ws, 'search_papers', {'query': 'anything'})
        self.assertNotIn('review_only', json.dumps(response))
        self.assertNotIn('relative_gain_percent', json.dumps(response))
        self.assertTrue(fixtures.human_facts()['review_only'])

    def test_manifest_is_serializable_stable_and_keeps_review_annotations_separate(self):
        manifest = fixtures.manifest()
        self.assertEqual(manifest['fixed_now'], fixtures.FIXED_NOW)
        self.assertEqual(len(manifest['sources']), 4)
        self.assertEqual(len(manifest['documents']), 2)
        self.assertEqual(len(manifest['publication_observations']), 4)
        encoded = json.dumps(manifest, sort_keys=True)
        self.assertEqual(encoded, json.dumps(fixtures.snapshot(), sort_keys=True))
        self.assertNotIn('review_only', encoded)
        self.assertNotIn('relative_gain_percent', encoded)
        manifest['documents'][0]['sections'][0]['start'] = -1
        self.assertGreaterEqual(fixtures.manifest()['documents'][0]['sections'][0]['start'], 0)

    def test_returned_data_is_isolated_across_calls(self):
        changed = fixtures.candidates()
        changed[2]['popularity']['score'] = -1
        changed[0]['authors'].append('mutated')
        self.assertGreater(fixtures.candidates()[2]['popularity']['score'], 0)
        self.assertEqual(len(fixtures.candidates()[0]['authors']), 1)
        facts = fixtures.human_facts()
        facts['limitations'].clear()
        self.assertTrue(fixtures.human_facts()['limitations'])

    def test_date_window_uses_first_submission_and_does_not_hide_topic_noise(self):
        recent = fixtures.call_tool(self.ws, 'search_papers',
                                    {'query': 'Agent memory', 'days': 7, 'sort_by': 'submittedDate'})
        self.assertEqual({s['fixture_key'] for s in recent['sources']}, {'A', 'B', 'D'})
        self.assertEqual(recent['time_window']['to'], fixtures.FIXED_NOW)
        trending = fixtures.call_tool(self.ws, 'discover_papers', {'kind': 'trending'})
        self.assertEqual(trending['sources'][0]['fixture_key'], 'C')
        other = fixtures.call_tool(self.ws, 'search_papers',
                                   {'query': 'unrelated wording', 'days': 7, 'sort_by': 'submittedDate'})
        self.assertEqual([s['url'] for s in recent['sources']], [s['url'] for s in other['sources']])

    def test_method_and_experiment_reads_have_original_document_spans(self):
        url = self.sources['A']['url']
        document = fixtures.document(url)
        method = fixtures.read_paper(url, full_text=True, section='method')
        experiment = fixtures.read_paper(url, full_text=True, query='experiment')
        self.assertNotEqual(method['text'], experiment['text'])
        for result in (method, experiment):
            self.assertEqual(result['coverage'], 'bounded_body_excerpt')
            self.assertTrue(result['full_text_ok'])
            self.assertFalse(result['truncated'])
            span = result['evidence_spans'][0]
            self.assertEqual(document['text'][span['start']:span['end']], result['text'])
            self.assertEqual(span['version'], document['version'])
        abstract = fixtures.read_paper(url)
        self.assertEqual(abstract['read_depth'], 'abstract')
        self.assertFalse(abstract['full_text_ok'])
        self.assertNotIn('Mean input tokens', abstract['text'])

    def test_combined_body_and_truncated_reads_keep_correct_offsets_and_budget(self):
        url = self.sources['B']['url']
        document = fixtures.document(url)
        combined = fixtures.read_paper(url, full_text=True, query='arbitrary question')
        self.assertEqual(len(combined['evidence_spans']), 2)
        self.assertIsNone(combined['query_matched'])
        bounded = fixtures.call_tool(
            SimpleNamespace(config=SimpleNamespace(read_char_budget=40)),
            'read_paper', {'url': url, 'full_text': True})
        self.assertLessEqual(len(bounded['text']), 40)
        self.assertTrue(bounded['truncated'])
        span = bounded['evidence_spans'][0]
        self.assertEqual(document['text'][span['start']:span['end']], bounded['text'])
        single_character = fixtures.read_paper(url, full_text=True, char_budget=1)
        self.assertEqual(len(single_character['text']), 1)
        self.assertTrue(single_character['truncated'])

    def test_publication_unknown_is_not_a_negative_verdict_or_a_quality_ranking(self):
        unknown = fixtures.verify_publication(self.ws, self.sources['A'])
        found = fixtures.call_tool(self.ws, 'verify_paper', {'url': self.sources['B']['url']})
        self.assertEqual(unknown['publication_status'], 'unresolved')
        self.assertEqual(unknown['peer_review_status'], 'not_verified')
        self.assertNotIn('is_published', unknown)
        self.assertEqual(found['publication_status'], 'publication_record_found')
        self.assertEqual(urlsplit(found['record_url']).hostname, 'example.invalid')
        self.assertTrue(found['synthetic'])
        self.assertNotIn('quality_score', found)

    def test_unknown_tools_sources_and_missing_body_never_fall_back_to_network(self):
        with patch('socket.socket', side_effect=AssertionError('NETWORK_FORBIDDEN')) as network:
            operations = [
                ('search_papers', {'query': 'memory'}),
                ('discover_papers', {'kind': 'trending'}),
                ('read_paper', {'url': self.sources['A']['url'], 'full_text': True}),
                ('verify_paper', {'url': self.sources['B']['url']}),
            ]
            for tool, params in operations:
                self.assertNotIn('error', fixtures.call_tool(self.ws, tool, params))
            for tool, params in [
                ('read_paper', {'url': 'https://arxiv.org/abs/9999.99999'}),
                ('read_paper', {'url': self.sources['C']['url'], 'full_text': True}),
                ('verify_paper', {'url': 'https://example.invalid/quality/unlisted'}),
                ('search_web', {'query': 'memory'}),
                ('read_webpage', {'url': 'https://example.invalid/quality/venue-b'}),
            ]:
                self.assertIn('error', fixtures.call_tool(self.ws, tool, params))
            network.assert_not_called()

    def test_discovery_limit_is_applied_to_both_kinds_and_invalid_limits_fail(self):
        for kind in ('recent', 'trending'):
            result = fixtures.call_tool(self.ws, 'discover_papers', {'kind': kind, 'limit': 2})
            self.assertNotIn('error', result)
            self.assertEqual(len(result['sources']), 2)
            for limit in (0, -1, True, 61, '2'):
                failure = fixtures.call_tool(self.ws, 'discover_papers',
                                             {'kind': kind, 'limit': limit})
                self.assertIn('error', failure)

    def test_production_discovery_dispatch_accepts_host_injected_limit(self):
        from lodestar.agent.dialogue import _execute
        from lodestar.config import Config
        ws = SimpleNamespace(config=Config(llm_mode='mock', search_mode='mock'), conn=None)
        with patch('lodestar.tools.registry.call_tool', side_effect=fixtures.call_tool) as adapter:
            for kind in ('recent', 'trending'):
                context = {'message': 'Find candidates', 'papers': [], 'candidates': []}
                allowed = set()
                params, result = _execute(ws, context, None, allowed,
                                          {'action': 'discover_papers', 'kind': kind, 'days': 7})
                self.assertEqual(params['limit'], 4)
                self.assertNotIn('error', result)
                self.assertTrue(result['sources'])
                self.assertLessEqual(len(result['sources']), params['limit'])
                self.assertEqual(allowed, {s['url'] for s in result['sources']})
                self.assertEqual(len(context['candidates']), len(result['sources']))
            self.assertEqual(adapter.call_count, 2)

    def test_production_search_read_and_verify_dispatch_accept_real_host_parameters(self):
        from lodestar.agent.dialogue import _execute
        from lodestar.config import Config
        ws = SimpleNamespace(config=Config(llm_mode='mock', search_mode='mock',
                                           read_char_budget=96), conn=None)
        context = {'message': 'Compare memory methods', 'papers': [], 'candidates': []}
        allowed = set()
        with patch('lodestar.tools.registry.call_tool', side_effect=fixtures.call_tool), \
                patch('lodestar.agent.dialogue.verify_publication', side_effect=fixtures.verify_publication), \
                patch('socket.socket', side_effect=AssertionError('NETWORK_FORBIDDEN')) as network:
            _, search = _execute(ws, context, None, allowed,
                                 {'action': 'search_papers', 'queries': ['Agent', 'memory'],
                                  'sort_by': 'submittedDate', 'days': 7})
            self.assertNotIn('error', search)
            params, read = _execute(ws, context, None, allowed,
                                   {'action': 'read_paper', 'url': self.sources['A']['url'],
                                    'full_text': True, 'query': 'experiment'})
            self.assertNotIn('error', read)
            self.assertEqual(params['char_budget'], 96)
            self.assertLessEqual(len(read['text']), 96)
            self.assertTrue(context['papers'])
            self.assertEqual(read['publication']['publication_status'], 'unresolved')
            _, publication = _execute(ws, context, None, allowed,
                                      {'action': 'verify_paper', 'url': self.sources['B']['url']})
            self.assertNotIn('error', publication)
            self.assertEqual(publication['publication_status'], 'publication_record_found')
            self.assertIn(publication['record_url'], allowed)
            network.assert_not_called()

    def test_invalid_requests_fail_explicitly_instead_of_silently_changing_inputs(self):
        for params in (
            {'url': self.sources['A']['url'], 'char_budget': 0},
            {'url': self.sources['A']['url'], 'char_budget': True},
            {'url': self.sources['A']['url'], 'section': 'unknown', 'full_text': True},
            {'url': self.sources['A']['url'], 'section': 'method'},
            {'url': self.sources['A']['url'], 'full_text': 'yes'},
        ):
            self.assertIn('error', fixtures.call_tool(self.ws, 'read_paper', params))
        self.assertIn('error', fixtures.call_tool(self.ws, 'search_papers', {'query': 'x', 'days': True}))
        self.assertIn('error', fixtures.call_tool(self.ws, 'search_papers', {'query': 'x', 'queries': ['x']}))
        self.assertIn('error', fixtures.call_tool(self.ws, 'read_paper', []))


if __name__ == '__main__':
    unittest.main()
