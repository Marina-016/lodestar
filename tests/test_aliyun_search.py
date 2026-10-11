"""Offline protocol/allowance checks; these fixtures are not live acceptance."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from lodestar.config import Config
from lodestar.providers import aliyun_search as provider
from lodestar.providers.search_quota import QuotaUnavailable, grant, reserve
from lodestar.tools.web_search import tool_search_web


class AllowanceTests(unittest.TestCase):
    def test_missing_expired_wrong_account_and_exhausted_allowances_stop(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'free.db'
            with self.assertRaises(QuotaUnavailable):
                reserve(path, 'test-key')
            grant(path, 'test-key', verified_remaining=5, call_limit=1, evidence='fixture')
            with self.assertRaises(QuotaUnavailable):
                reserve(path, 'other-key')
            with patch('lodestar.providers.search_quota.time.time', return_value=time.time() + 3601):
                with self.assertRaises(QuotaUnavailable):
                    reserve(path, 'test-key')
            reserve(path, 'test-key')
            with self.assertRaises(QuotaUnavailable):
                reserve(path, 'test-key')
            self.assertNotIn(b'test-key', path.read_bytes())

    def test_parallel_process_style_reservations_cannot_overdraw(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'free.db'
            grant(path, 'fixture', verified_remaining=20, call_limit=3, evidence='fixture')
            def attempt(_):
                try:
                    reserve(path, 'fixture')
                    return True
                except QuotaUnavailable:
                    return False
            with ThreadPoolExecutor(max_workers=8) as pool:
                self.assertEqual(sum(pool.map(attempt, range(20))), 3)

    def test_invalid_grants_never_authorize_calls(self):
        with tempfile.TemporaryDirectory() as folder:
            for remaining, limit, evidence in [(5, 6, 'fixture'), (2001, 1, 'fixture'),
                                                (1, 0, 'fixture'), (True, 1, 'fixture'), (1, 1, '')]:
                with self.assertRaises(ValueError):
                    grant(Path(folder)/'free.db', 'key', verified_remaining=remaining,
                          call_limit=limit, evidence=evidence)


class SearchTests(unittest.TestCase):
    def test_unverified_backend_never_opens_transport(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'DASHSCOPE_API_KEY': 'fixture'}), \
                patch.object(provider, '_invoke') as invoke:
            ws = SimpleNamespace(config=Config(web_search_backend='aliyun_mcp', workspace_dir=Path(folder)))
            result = tool_search_web(ws, 'Sora')
            self.assertEqual(result['failure_kind'], 'free_quota_unverified')
            invoke.assert_not_called()

    def test_failed_attempt_has_no_refund_or_automatic_retry(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'DASHSCOPE_API_KEY': 'fixture'}):
            path = Path(folder)/'free.db'
            grant(path, 'fixture', verified_remaining=5, call_limit=1, evidence='fixture')
            with patch.object(provider, '_invoke', new=AsyncMock(side_effect=RuntimeError('fixture secret'))) as invoke:
                with self.assertRaises(provider.SearchUnavailable) as error:
                    provider.request('Sora', quota_path=path)
                self.assertNotIn('secret', str(error.exception))
                with self.assertRaises(QuotaUnavailable):
                    provider.request('Sora', quota_path=path)
                self.assertEqual(invoke.await_count, 1)

    def test_inspect_is_metadata_only_and_does_not_need_allowance(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {'DASHSCOPE_API_KEY': 'fixture'}), \
                patch.object(provider, '_invoke', new=AsyncMock(return_value={'tools': []})) as invoke:
            self.assertEqual(provider.request(None, quota_path=Path(folder)/'missing.db'), {'tools': []})
            self.assertIsNone(invoke.call_args.args[2])

    def test_official_sdk_discovers_query_tool_and_calls_it_once(self):
        try:
            import mcp
            import mcp.client.streamable_http
        except ImportError:
            self.skipTest('optional MCP SDK not installed')
        tool = SimpleNamespace(name='server-advertised-search', inputSchema={
            'type': 'object', 'properties': {'query': {'type': 'string'}, 'count': {'type': 'integer'}}, 'required': ['query']})
        session = SimpleNamespace(initialize=AsyncMock(), list_tools=AsyncMock(return_value=SimpleNamespace(tools=[tool])),
            call_tool=AsyncMock(return_value=SimpleNamespace(isError=False,
                model_dump=lambda **_: {'content': [{'type': 'text', 'text': 'fixture'}]})))
        @asynccontextmanager
        async def transport(*args, **kwargs):
            self.assertEqual(args[0], provider.ENDPOINT)
            self.assertEqual(kwargs['headers'], {'Authorization': 'Bearer fixture'})
            yield (None, None, None)
        @asynccontextmanager
        async def client(*args, **kwargs):
            yield session
        with patch.object(mcp.client.streamable_http, 'streamablehttp_client', transport), \
                patch.object(mcp, 'ClientSession', client):
            result = asyncio.run(provider._invoke('fixture', 5, 'OpenAI Sora'))
        session.call_tool.assert_awaited_once_with('server-advertised-search', {'query': 'OpenAI Sora', 'count': 6})
        self.assertEqual(result['content'][0]['text'], 'fixture')

    def test_json_sources_preserve_provenance_without_making_up_dates(self):
        payload = {'results': [{'title': 'Sora', 'url': 'https://openai.com/sora/', 'snippet': 'Video'},
                               {'title': 'Invalid', 'url': 'javascript:alert(1)'}]}
        for result in [{'structuredContent': payload}, {'content': [{'type': 'text', 'text': json.dumps(payload)}]}]:
            normalized = provider.normalize(result, 'OpenAI Sora', 3)
            self.assertEqual(len(normalized['sources']), 1)
            self.assertEqual(normalized['sources'][0]['date'], '')
            self.assertEqual(normalized['sources'][0]['query'], 'OpenAI Sora')
            self.assertEqual(normalized['sources'][0]['provider'], 'aliyun_websearch_mcp')

    def test_unrecognized_text_is_preserved_without_inventing_source_cards(self):
        result = provider.normalize({'content': [{'type': 'text', 'text': 'Provider text fixture'}]}, 'test', 3)
        self.assertEqual(result['sources'], [])
        self.assertEqual(result['provider_text'], 'Provider text fixture')
        with self.assertRaises(provider.SearchUnavailable):
            provider.normalize({'content': []}, 'test', 3)
