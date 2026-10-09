import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import requests

from lodestar.tools.arxiv_client import ArxivClient, ArxivUnavailable, Feed, _retry_delay
from lodestar.tools.arxiv_search import build_query, tool_search_papers
from lodestar.tools.paper_read import _fetch_abstract
from lodestar.config import Config
from types import SimpleNamespace

XML = '<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>https://arxiv.org/abs/2601.00001</id><title>Agent</title><summary>Abstract</summary><published>2026-01-01</published></entry></feed>'


def response(status=200, body=XML, headers=None):
    value = requests.Response()
    value.status_code = status
    value._content = body.encode()
    value.headers.update(headers or {})
    return value


class Clock:
    def __init__(self): self.now = 1000.0
    def time(self): return self.now
    def sleep(self, seconds): self.now += seconds


class ArxivClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)/'client.sqlite'
        self.clock = Clock()
        self.client = ArxivClient(self.path, clock=self.clock.time, sleep=self.clock.sleep)

    def tearDown(self): self.temp.cleanup()

    def test_cached_metadata_shared_by_clients_and_keeps_fetch_time(self):
        with patch('lodestar.tools.arxiv_client.requests.get', return_value=response()) as request:
            first = self.client.query({'search_query': 'cat:cs.AI'})
            self.clock.now += 20
            second = ArxivClient(self.path, clock=self.clock.time, sleep=self.clock.sleep).query({'search_query':'cat:cs.AI'})
        self.assertEqual(request.call_count, 1)
        self.assertFalse(first.cache_hit)
        self.assertTrue(second.cache_hit)
        self.assertEqual(first.fetched_at, second.fetched_at)

    def test_rate_limit_cools_all_clients_without_retrying(self):
        with patch('lodestar.tools.arxiv_client.requests.get', return_value=response(429, headers={'Retry-After':'30'})) as request:
            for query in ('agent', 'memory'):
                with self.assertRaises(ArxivUnavailable) as caught:
                    self.client.query({'search_query':query})
                self.assertEqual(caught.exception.failure_kind,'rate_limited')
            self.assertEqual(request.call_count,1)
        self.clock.now += 31
        with patch('lodestar.tools.arxiv_client.requests.get', return_value=response()) as request:
            self.assertFalse(self.client.query({'search_query':'agent'}).cache_hit)
        request.assert_called_once()

    def test_cached_success_still_available_during_outage(self):
        with patch('lodestar.tools.arxiv_client.requests.get', return_value=response()):
            self.client.query({'search_query':'agent'})
        with patch('lodestar.tools.arxiv_client.requests.get', side_effect=requests.Timeout()):
            with self.assertRaises(ArxivUnavailable):
                self.client.query({'search_query':'memory'})
        with patch('lodestar.tools.arxiv_client.requests.get') as request:
            self.assertTrue(self.client.query({'search_query':'agent'}).cache_hit)
            request.assert_not_called()

    def test_invalid_feed_is_not_cached(self):
        with patch('lodestar.tools.arxiv_client.requests.get', side_effect=[response(body='<html/>'), response()]):
            with self.assertRaises(ValueError): self.client.query({'search_query':'agent'})
            self.assertFalse(self.client.query({'search_query':'agent'}).cache_hit)

    def test_separate_instances_serialize_and_space_requests(self):
        starts, active = [], []
        lock = threading.Lock()
        def get(*args, **kwargs):
            with lock:
                starts.append(time.monotonic())
                active.append(1)
                self.assertEqual(len(active),1)
            time.sleep(.05)
            with lock: active.pop()
            return response()
        # Initialize schema before exercising simultaneous cache misses.
        with patch('lodestar.tools.arxiv_client.requests.get', return_value=response()):
            self.client.query({'search_query':'warmup'})
        with closing(sqlite3.connect(self.path)) as conn: conn.execute('UPDATE state SET last_start=0'); conn.commit()
        with patch('lodestar.tools.arxiv_client.requests.get', side_effect=get):
            with ThreadPoolExecutor(2) as pool:
                futures = [pool.submit(ArxivClient(self.path).query, {'search_query':q}) for q in ('a','b')]
                for future in futures: future.result()
        self.assertGreaterEqual(starts[1]-starts[0],3.05)
        with closing(sqlite3.connect(self.path)) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM requests').fetchone()[0],3)

    def test_retry_after_supports_http_date_and_default(self):
        self.assertEqual(_retry_delay('Thu, 01 Jan 1970 00:20:00 GMT',1000),200)
        self.assertEqual(_retry_delay(None,1000),60)

    def test_plain_query_does_not_become_implicit_or(self):
        self.assertEqual(build_query('agent memory'),'abs:agent AND abs:memory')
        self.assertEqual(build_query('"large language model" reasoning'),'abs:"large language model" AND abs:reasoning')
        self.assertEqual(build_query('cat:cs.AI OR cat:cs.CL'),'cat:cs.AI OR cat:cs.CL')
        with self.assertRaises(ValueError): build_query('"unfinished')

    def test_days_are_compiled_to_actual_date_filter(self):
        ws = SimpleNamespace(config=Config(search_mode='live'))
        with patch('lodestar.tools.arxiv_client.query', return_value=Feed(XML,'2026-01-01T00:00:00+00:00',False)) as query:
            result = tool_search_papers(ws,'agent',days=7)
        self.assertIn('submittedDate:[',query.call_args.args[0]['search_query'])
        self.assertEqual(result['days'],7)
        self.assertEqual(result['sources'][0]['retrieved_at'],'2026-01-01T00:00:00+00:00')

    def test_abstract_uses_same_metadata_client(self):
        with patch('lodestar.tools.arxiv_client.query', return_value=Feed(XML,'2026-01-01T00:00:00+00:00',False)) as query:
            abstract = _fetch_abstract('2601.00001',15)
        self.assertEqual(query.call_args.args[0]['id_list'],'2601.00001')
        self.assertEqual(abstract['abstract'],'Abstract')

    def test_empty_recent_discovery_browses_ai_category(self):
        from lodestar.tools.discovery import discover
        ws = SimpleNamespace(config=Config(search_mode='live'))
        with patch('lodestar.tools.discovery._search_arxiv', return_value=[]) as search:
            result = discover(ws,'',kind='recent')
        self.assertEqual(search.call_args.args[0],'cat:cs.AI')
        self.assertEqual(result['status'],'ok')

    def test_search_returns_complete_abstract_beyond_legacy_snippet(self):
        from lodestar.tools.arxiv_search import _search_arxiv
        abstract = 'A meaningful abstract sentence. ' * 40 + 'IMPORTANT FINAL RESULT'
        xml = XML.replace('Abstract',abstract)
        with patch('lodestar.tools.arxiv_client.query', return_value=Feed(xml,'2026-01-01T00:00:00+00:00',False)):
            paper = _search_arxiv('agent')[0]
        self.assertEqual(len(paper['snippet']),600)
        self.assertEqual(paper['abstract'],abstract)
        self.assertTrue(paper['abstract'].endswith('IMPORTANT FINAL RESULT'))
