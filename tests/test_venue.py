import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from lodestar import venue
from lodestar.config import Config


def response(data):
    return SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: data)


class VenueTests(unittest.TestCase):
    def test_preprint_does_not_short_circuit_publication_lookup(self):
        first = Mock(return_value=venue._result(venue.PREPRINT_VENUE, False, {}, 'first'))
        second = Mock(return_value=venue._result('Conference', True, {}, 'second', 'https://publisher/paper'))
        cfg = Config(search_mode='live', venue_providers=('first', 'second'))
        with patch.dict(venue._FETCHERS, {'first': first, 'second': second}, clear=True):
            result, _ = venue.enrich_papers_venues(cfg, [{'source_type': 'paper',
                'url': 'https://arxiv.org/abs/2601.00001', 'title': 'Title'}], retry=False)
        second.assert_called_once()
        self.assertEqual(result[0]['venue'], 'Conference')
        self.assertEqual(result[0]['record_url'], 'https://publisher/paper')

    def test_missing_metadata_is_not_invented_preprint(self):
        result = venue._result(None, False, {}, 'provider')
        self.assertIsNone(result['venue'])

    def test_semanticscholar_types_do_not_alone_prove_publication(self):
        with patch('lodestar.venue.requests.get', return_value=response({
                'publicationTypes': ['Review'], 'externalIds': {'DOI': 'example'}})):
            result = venue._fetch_semanticscholar('2601.00001', 'Title', 3, 'agent')
        self.assertFalse(result['is_published'])
        self.assertIsNone(result['venue'])
        self.assertEqual(result['external_ids']['DOI'], 'example')

    def test_openalex_matches_title_and_checks_nonprimary_locations(self):
        data = {'results': [{'display_name': 'An exact title.', 'id': 'record',
            'primary_location': {'source': {'type': 'repository', 'display_name': 'arXiv'}},
            'locations': [{'version': 'publishedVersion', 'landing_page_url': 'https://publisher/paper',
                           'source': {'type': 'journal', 'display_name': 'Journal'}}]}]}
        with patch('lodestar.venue.requests.get', return_value=response(data)) as request:
            result = venue._fetch_openalex('2601.00001', 'An exact title', 3, 'agent')
        self.assertEqual(request.call_args.kwargs['params']['search'], 'An exact title')
        self.assertTrue(result['is_published'])
        self.assertEqual(result['venue'], 'Journal')

    def test_similar_published_title_does_not_beat_exact_identity(self):
        exact = {'title': 'Exact'}
        other = {'title': 'Different'}
        self.assertEqual(venue._pick_best([(exact, 1.0, False), (other, .96, True)])[0], exact)
        self.assertIsNone(venue._pick_best([(other, .90, True)]))

    def test_openalex_repository_record_cannot_exclude_published_duplicate(self):
        data = {'results': [
            {'display_name': 'Title', 'primary_location': {'source': {'type': 'repository', 'display_name': 'arXiv (Cornell University)'}}},
            {'display_name': 'Title', 'primary_location': {'version': 'publishedVersion',
                'source': {'type': 'conference', 'display_name': 'Conference'}}}]}
        with patch('lodestar.venue.requests.get', return_value=response(data)):
            result = venue._fetch_openalex('2601.00001', 'Title', 3, 'agent')
        self.assertTrue(result['is_published'])
        self.assertEqual(result['venue'], 'Conference')

    def test_openalex_arxiv_display_name_is_a_preprint_record_not_publication(self):
        data = {'results': [{'display_name': 'Title', 'primary_location': {
            'source': {'type': 'repository', 'display_name': 'arXiv (Cornell University)'}}}]}
        with patch('lodestar.venue.requests.get', return_value=response(data)):
            result = venue._fetch_openalex('2601.00001', 'Title', 3, 'agent')
        self.assertFalse(result['is_published'])
        self.assertEqual(result['venue'], venue.PREPRINT_VENUE)

    def test_rate_limit_cooldown_shared_across_native_lookups(self):
        provider = Mock(return_value={'_rate_limited': True})
        cfg = Config(search_mode='live', venue_providers=('provider',))
        cooldowns = {}
        with patch.dict(venue._FETCHERS, {'provider': provider}, clear=True):
            for index in range(2):
                result, _ = venue.enrich_papers_venues(cfg, [{'source_type': 'paper',
                    'url': f'https://arxiv.org/abs/2601.0000{index}', 'title': 'Title'}],
                    retry=False, cooldowns=cooldowns)
        provider.assert_called_once()
        self.assertTrue(result[0]['venue_lookups'][0]['cooldown'])
