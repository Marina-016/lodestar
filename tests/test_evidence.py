import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from lodestar.agent.evidence import assess, answer_context, merge_sources, publication_lookup_fresh, verify_publication
from lodestar.config import Config
from lodestar.tools.arxiv_search import tool_search_papers


class EvidenceTests(unittest.TestCase):
    def test_latest_default_and_explicit_opt_out(self):
        ws = SimpleNamespace(config=Config(search_mode='live'))
        for days, expected in [(None, 90), (0, 0), (7, 7)]:
            with patch('lodestar.tools.arxiv_search._search_arxiv', return_value=[]) as search:
                result = tool_search_papers(ws, 'AI4AI', sort_by='submittedDate', days=days)
            self.assertEqual(result['time_window']['days'], expected)
            self.assertEqual(result['time_window']['default_applied'], days is None)
            self.assertEqual(search.call_args.kwargs['since'] is None, expected == 0)

    def test_invalid_days_are_tool_errors(self):
        ws = SimpleNamespace(config=Config(search_mode='live'))
        for days in ('seven', True, -1, 366):
            self.assertIn('error', tool_search_papers(ws, 'AI4AI', days=days))

    def test_updates_do_not_promote_old_papers(self):
        facts = assess([
            {'url': 'old', 'date': '2020-01-01', 'updated_at': '2026-10-09'},
            {'url': 'recent', 'date': '2026-10-01'},
            {'url': 'unknown', 'date': None},
        ], datetime(2026, 10, 10, tzinfo=timezone.utc))
        self.assertEqual(facts['recent_count'], 1)
        self.assertFalse(facts['sources'][0]['within_recent_window'])
        self.assertIsNone(facts['sources'][2]['within_recent_window'])
        self.assertEqual(facts['publication_checks']['publication_record_found'], 0)

    def test_publication_record_does_not_prove_peer_review(self):
        ws = SimpleNamespace(config=Config(search_mode='live', enrich_venues=True))
        source = {'url': 'paper', 'title': 'Study'}
        with patch('lodestar.agent.evidence.venue.enrich_papers_venues', return_value=(
                [{**source, 'venue': 'Conference', 'is_published': True, 'venue_note': 'Crossref'}], 'ok')):
            result = verify_publication(ws, source)
        self.assertEqual(result['publication_status'], 'publication_record_found')
        self.assertEqual(result['peer_review_status'], 'not_verified')

    def test_lookup_failure_is_unknown_not_unpublished(self):
        ws = SimpleNamespace(config=Config(search_mode='live', enrich_venues=True))
        with patch('lodestar.agent.evidence.venue.enrich_papers_venues', side_effect=RuntimeError('offline')):
            result = verify_publication(ws, {'url': 'paper'})
        self.assertEqual(result['publication_status'], 'unresolved')
        self.assertNotIn('is_published', result)

    def test_merge_preserves_checked_metadata_and_full_body(self):
        first = {'url': 'paper', 'date': '2026-10-01', 'content': 'Full body', 'read_depth': 'full',
                 'publication_status': 'publication_record_found', 'venue': 'Journal',
                 'external_ids': {'DOI': 'known'}}
        new = {'url': 'paper', 'content': 'Abstract', 'read_depth': 'abstract',
               'date': '', 'venue': None, 'external_ids': {}, 'publication_status': 'not_checked'}
        merged = merge_sources([first, new])[0]
        self.assertEqual(merged['date'], first['date'])
        self.assertEqual(merged['venue'], 'Journal')
        self.assertEqual(merged['content'], 'Full body')
        self.assertEqual(merged['external_ids'], {'DOI': 'known'})
        self.assertEqual(first['content'], 'Full body')

    def test_answer_packet_includes_evidence_once_not_raw_logs(self):
        source = {'url': 'paper', 'abstract': 'COMPLETE ABSTRACT'}
        context = {'current_time': '2026-10-10', 'papers': [source], 'candidates': [source],
            'dialogue_events': [{'action': 'search_papers', 'params': {'query': 'agent'},
                                'result': {'sources': [source], 'time_window': {'days': 7}}}]}
        packet = answer_context(context)
        import json
        self.assertEqual(json.dumps(packet).count('COMPLETE ABSTRACT'), 1)
        self.assertEqual(packet['operations'][0]['time_window']['days'], 7)
        self.assertNotIn('dialogue_events', packet)

    def test_web_sources_have_no_implied_publication_check(self):
        facts = assess([{'url': 'https://example.com', 'source_type': 'web'}])
        self.assertNotIn('publication_status', facts['sources'][0])

    def test_disabled_lookup_does_not_contact_provider(self):
        ws = SimpleNamespace(config=Config(enrich_venues=False))
        with patch('lodestar.agent.evidence.venue.enrich_papers_venues') as lookup:
            result = verify_publication(ws, {'url': 'paper'})
        lookup.assert_not_called()
        self.assertEqual(result['publication_status'], 'unresolved')

    def test_failed_refresh_does_not_reuse_stale_provider_as_new_check(self):
        ws = SimpleNamespace(config=Config(search_mode='live'))
        old = {'url': 'paper', 'venue_note': 'old provider', 'is_published': True, 'venue': 'Journal'}
        with patch('lodestar.agent.evidence.venue.enrich_papers_venues', return_value=([{'venue': None}], 'no matches')):
            result = verify_publication(ws, old)
        self.assertEqual(result['publication_status'], 'unresolved')

    def test_provider_outage_is_not_an_empty_publication_search(self):
        ws = SimpleNamespace(config=Config(search_mode='live'))
        with patch('lodestar.agent.evidence.venue.enrich_papers_venues', return_value=(
                [{'venue_lookups': [{'provider': 'provider', 'status': 'unavailable'}]}], 'failed')):
            result = verify_publication(ws, {'url': 'paper'})
        self.assertIn('providers were unavailable', result['publication_evidence'])
        self.assertEqual(result['lookups'][0]['status'], 'unavailable')

    def test_publication_lookup_cache_expires(self):
        now = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
        self.assertTrue(publication_lookup_fresh({'verified_at': '2026-10-10T11:55:00+00:00'}, now))
        self.assertFalse(publication_lookup_fresh({'verified_at': '2026-10-10T11:00:00+00:00'}, now))
        self.assertFalse(publication_lookup_fresh({'verified_at': '2026-10-10T12:05:00+00:00'}, now))
        self.assertFalse(publication_lookup_fresh({}, now))

    def test_unchecked_is_separate_from_an_unsuccessful_lookup(self):
        facts = assess([{'url': 'paper/unchecked', 'source_type': 'paper'},
                        {'url': 'paper/checked', 'source_type': 'paper', 'publication_status': 'unresolved'}])
        self.assertEqual(facts['publication_checks']['not_checked'], 1)
        self.assertEqual(facts['publication_checks']['unresolved'], 1)
        self.assertNotIn('authority', facts)
