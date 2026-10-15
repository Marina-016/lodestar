"""Acceptance-runner checks do not equate functional completion with quality."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from concurrent.futures import ThreadPoolExecutor

from lodestar.config import Config
from lodestar.llm import LLMError
from scripts.eval_workspace import ModelBudget, run


class WorkspaceAcceptanceTests(unittest.TestCase):
    def test_model_guard_counts_physical_attempts_atomically_and_fails_closed(self):
        budget = ModelBudget(3)
        request = SimpleNamespace(method='POST', url=SimpleNamespace(path='/v1/messages'), extensions={})
        def reserve(_):
            try:
                budget.reserve(request)
                return True
            except LLMError:
                return False
        with ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(reserve, range(20)))
        self.assertEqual(sum(outcomes), 3)
        self.assertEqual(budget.used, 3)
        self.assertEqual(budget.blocked, 17)
        budget.reserve(SimpleNamespace(method='GET', url=SimpleNamespace(path='/v1/models')))
        self.assertEqual(budget.used, 3)

    def test_cancelled_acceptance_cannot_start_another_inference_attempt(self):
        budget = ModelBudget(10)
        budget.stopped = True
        with self.assertRaises(LLMError):
            budget.reserve(SimpleNamespace(method='POST', url=SimpleNamespace(path='/v1/messages')))
        self.assertEqual(budget.used, 0)

    def test_transport_observer_keeps_bytes_unchanged_and_does_not_store_content_or_credentials(self):
        import httpx
        budget = ModelBudget(2)
        request = httpx.Request('POST', 'https://secret-host.example/v1/messages?token=PRIVATE_CANARY', headers={'Authorization': 'PRIVATE_CANARY'})
        budget.reserve(request)
        chunks = [b'ev', b'ent: message_start\ndata: {"type":"message_start","message":{"content":"PRIVATE_CANARY"}}\n\n',
                  b'event: CUSTOM_PRIVATE_CANARY\ndata: {"type":"CUSTOM_PRIVATE_CANARY"}\n\n',
                  b'event: message_stop\ndata: {"type":"message_stop"}\n\n']
        class Stream(httpx.SyncByteStream):
            def __iter__(self):
                yield from chunks
        response = httpx.Response(200, request=request, headers={'content-type': 'text/event-stream; charset=utf-8'}, stream=Stream())
        budget.observe_response(response)
        self.assertEqual(b''.join(response.iter_bytes()), b''.join(chunks))
        response.close()
        record = budget.responses[0]
        self.assertEqual(record['body_shape'], 'sse')
        self.assertEqual(record['sse_events'], {'message_start': 1, 'message_stop': 1, 'other': 1})
        self.assertEqual(record['data_types'], record['sse_events'])
        self.assertEqual(record['bytes_received'], sum(map(len, chunks)))
        self.assertEqual(record['attempt'], 1)
        self.assertTrue(record['closed'])
        self.assertNotIn('PRIVATE_CANARY', json.dumps(record))
        self.assertNotIn('secret-host', json.dumps(record))

    def test_non_sse_success_response_is_observable_without_storing_its_error_body(self):
        import httpx
        budget = ModelBudget(1)
        request = httpx.Request('POST', 'https://example.test/v1/messages')
        budget.reserve(request)
        body = b'{"error":"PRIVATE_CANARY"}\n'
        class Stream(httpx.SyncByteStream):
            def __iter__(self):
                yield body
        response = httpx.Response(200, request=request, headers={'content-type': 'application/json'}, stream=Stream())
        budget.observe_response(response)
        self.assertEqual(response.read(), body)
        self.assertEqual(budget.responses[0]['body_shape'], 'json_like')
        self.assertEqual(budget.responses[0]['data_types'], {})
        self.assertNotIn('PRIVATE_CANARY', json.dumps(budget.responses))

    def test_offline_http_runs_use_a_new_database_and_persist_honest_report(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            original = root/'original.db'
            original.write_bytes(b'USER_DATABASE_CANARY')
            cfg = Config(llm_mode='mock', search_mode='mock', db_path=original,
                         project_model_allowed_repository='https://github.com/private/repo',
                         project_model_allowed_paths=('private.py',))
            with contextlib.redirect_stdout(io.StringIO()):
                report = run(cfg, root/'acceptance')
            self.assertEqual(original.read_bytes(), b'USER_DATABASE_CANARY')
            self.assertEqual(report['model_attempts'], 0)
            self.assertEqual(report['semantic_review'], 'not_applicable_offline_fixture')
            self.assertTrue(all(report['checks'].values()))
            self.assertEqual(len(report['turns']), 2)
            self.assertEqual(report['remaining_runs_at_exit'], 0)
            saved = json.loads((root/'acceptance'/'report.json').read_text(encoding='utf-8'))
            self.assertEqual(saved, report)
            self.assertNotIn('lodestar-token', json.dumps(report))
            with self.assertRaises(FileExistsError):
                run(cfg, root/'acceptance')

    def test_live_search_cannot_use_a_paid_backend_and_invalid_limits_fail(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(ValueError):
                run(Config(llm_mode='mock', search_mode='live', web_search_backend='aliyun_mcp'),
                    Path(temp)/'paid')
        for limit in (0, 101):
            with self.assertRaises(ValueError):
                ModelBudget(limit)
