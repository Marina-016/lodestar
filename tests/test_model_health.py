from dataclasses import replace
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import requests

from lodestar.chat_settings import snapshot
from lodestar.config import Config
from lodestar.llm import LLMError, error_details
from lodestar.providers.errors import classify, raise_for_status
from lodestar.web import model_health as health
from lodestar.web.server import Conflict, WebApp, make_server


class ErrorClassificationTests(unittest.TestCase):
    def error(self, status, body):
        response = requests.Response()
        response.status_code = status
        response._content = json.dumps(body).encode()
        try:
            raise_for_status(response)
        except LLMError as error:
            return error_details(error)
        self.fail('HTTP rejection expected')

    def test_explicit_free_tier_stop_is_not_generic_authentication(self):
        result = self.error(403, {'error': {'code': 'AllocationQuota.FreeTierOnly',
                                           'message': 'PRIVATE_BODY_CANARY'}})
        self.assertEqual(result['code'], 'quota_exhausted')
        self.assertEqual(result['http_status'], 403)
        self.assertNotIn('PRIVATE_BODY_CANARY', json.dumps(result))

    def test_token_rate_quota_and_unknown_http_errors_do_not_mean_free_exhaustion(self):
        for code in ('insufficient_quota','Throttling.AllocationQuota','rate_limit_error'):
            with self.subTest(code=code):
                self.assertEqual(self.error(429, {'error': {'code': code}})['code'], 'rate_limit')
        self.assertEqual(self.error(400, {'error': {'message':'PRIVATE_MODEL_PARAMS'}})['code'], 'http_error')
        self.assertEqual(self.error(403, {'error': {'type':'permission_error'}})['code'], 'authentication')

    def test_sdk_structured_body_and_exact_model_error_are_allowlisted(self):
        for body in ({'error': {'code':'model_not_found'}}, {'message':'Model not exist.'}):
            cause = RuntimeError('PRIVATE_BODY')
            cause.body = body
            cause.status_code = 400
            error = LLMError('safe')
            error.__cause__ = cause
            self.assertEqual(error_details(error)['code'], 'model_unavailable')
        self.assertIsNone(classify({'error': {'message': 'The last model said quota is exhausted in the prompt'}}))

    def test_stream_error_parser_is_bounded_and_does_not_store_provider_body(self):
        response = requests.Response()
        response.status_code = 403
        response._content = False
        response.iter_content = Mock(return_value=iter([b'{"error":{"code":"AllocationQuota.FreeTierOnly"}}']))
        with self.assertRaises(LLMError) as caught:
            raise_for_status(response)
        self.assertEqual(error_details(caught.exception)['code'], 'quota_exhausted')
        self.assertIs(response._content, False)


class ModelHealthTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='lodestar-models-')
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.cfg = Config(llm_mode='live', search_mode='mock', model='stale-model',
            db_path=root/'web.db', workspace_dir=root, llm_base_url='https://gateway.example')
        self.factory = Mock()
        self.app = WebApp(self.cfg, llm_factory=self.factory)
        self.addCleanup(self.app.close)
        self.good = replace(self.cfg, model='good-model')

    def record(self, cfg, status, **kwargs):
        with self.app.workspace() as ws, ws.conn:
            health.record(ws.conn, cfg, status, **kwargs)

    def test_initial_load_constructs_no_client_and_never_guesses_healthy_models(self):
        state = self.app.state()
        self.assertEqual(state['model_selection']['availability']['stale-model'], 'unverified')
        self.assertIsNone(state['model_selection']['recommended_model'])
        self.factory.assert_not_called()

    def test_exhausted_default_is_hidden_even_if_directory_still_lists_it(self):
        self.record(self.cfg, 'quota_exhausted')
        self.record(self.good, 'available')
        client = Mock()
        client.list_models.return_value = ['stale-model','good-model','unknown-model']
        self.factory.return_value = client
        selection = self.app.models('main')
        self.assertEqual(selection['models'], ['good-model','unknown-model'])
        self.assertEqual(selection['recommended_model'], 'good-model')
        self.assertEqual(selection['availability']['unknown-model'], 'unverified')
        self.assertNotIn('stale-model', selection['reasoning'])
        self.assertEqual(selection['unavailable'][0]['code'], 'quota_exhausted')
        client.dialogue_step.assert_not_called()

    def test_missing_default_is_not_injected_into_real_catalog(self):
        client = Mock()
        client.list_models.return_value = ['unknown-model']
        self.factory.return_value = client
        self.assertEqual(self.app.models('main')['models'], ['unknown-model'])

    def test_known_rejected_model_is_blocked_before_queue_or_inference(self):
        self.record(self.cfg, 'quota_exhausted')
        with self.assertRaises(Conflict):
            self.app.submit('main','Do not send','blocked')
        self.factory.assert_not_called()
        self.assertIsNone(self.app.state()['run'])

    def test_recent_matching_success_can_be_selected_without_inference_or_enabling(self):
        self.record(self.cfg, 'model_unavailable')
        self.record(self.good, 'available')
        self.app.update('main', {'settings': {'enabled':False}})
        self.assertEqual(self.app.select_model_default('main'), {'changed':True,'model':'good-model'})
        self.assertEqual(self.app.state()['configuration']['model'], 'good-model')
        self.assertFalse(self.app.state()['enabled'])
        self.factory.assert_not_called()

    def test_explicit_unknown_choice_is_not_overridden_or_automatically_probed(self):
        self.record(self.good, 'available')
        self.app.update('main', {'settings': {'model':'my-manual-choice'}})
        self.assertFalse(self.app.select_model_default('main')['changed'])
        self.assertEqual(self.app.state()['configuration']['model'], 'my-manual-choice')
        self.factory.assert_not_called()

    def test_active_run_and_other_thinking_configuration_do_not_trigger_switching(self):
        self.record(self.good, 'available')
        with self.app.workspace() as ws, ws.conn:
            ws.conn.execute("INSERT INTO web_runs(id,item_id,request_key,status,message,config) "
                "VALUES('active','main','active','running','fixture','{}')")
        self.assertFalse(self.app.select_model_default('main')['changed'])
        with self.app.workspace() as ws, ws.conn:
            ws.conn.execute("UPDATE web_runs SET status='completed'")
        self.app.update('main', {'settings':{'thinking':'on'}})
        self.assertFalse(self.app.select_model_default('main')['changed'])

    def test_restore_requires_explicit_action_and_only_returns_unverified(self):
        self.record(self.cfg,'quota_exhausted')
        self.app.restore_model('main','stale-model')
        state = self.app.state()['model_selection']
        self.assertFalse(state['current_blocked'])
        self.assertEqual(state['availability']['stale-model'],'unverified')
        self.assertIsNone(state['recommended_model'])
        self.factory.assert_not_called()

    def test_request_rejection_is_not_a_model_wide_quota_claim(self):
        self.record(self.cfg,'request_rejected')
        selection = self.app.state()['model_selection']
        self.assertEqual(selection['unavailable'][0]['code'],'request_rejected')
        self.app.update('main', {'settings':{'reasoning':'default'}})
        self.assertFalse(self.app.state()['model_selection']['current_blocked'])

    def test_account_endpoint_mode_and_stale_success_are_separate(self):
        with patch.dict('os.environ', {'ANTHROPIC_API_KEY':'account-a'}):
            self.record(self.cfg,'quota_exhausted')
        with patch.dict('os.environ', {'ANTHROPIC_API_KEY':'account-b'}):
            self.assertFalse(self.app.state()['model_selection']['current_blocked'])
        self.record(self.good,'available',observed_at=time.time()-health.FRESH_SECONDS-1)
        self.assertIsNone(self.app.state()['model_selection']['recommended_model'])
        other = replace(self.cfg,llm_base_url='https://different.example')
        self.assertNotEqual(health.service_key(self.cfg),health.service_key(other))
        self.assertNotEqual(health.service_key(self.cfg),health.service_key(replace(self.cfg,llm_mode='mock')))

    def test_late_old_success_cannot_resurrect_exhausted_or_restored_models(self):
        self.record(self.cfg,'quota_exhausted',observed_at=20)
        self.record(self.cfg,'available',observed_at=10)
        self.assertTrue(self.app.state()['model_selection']['current_blocked'])
        self.app.restore_model('main','stale-model')
        self.record(self.cfg,'quota_exhausted',observed_at=30)
        self.assertFalse(self.app.state()['model_selection']['current_blocked'])

    def test_success_and_failure_observations_are_real_and_mode_scoped(self):
        with self.app.workspace() as ws, ws.conn:
            health.observe(ws.conn,self.cfg,{'status':'error','model_error':{'code':'quota_exhausted'}},None,10)
            health.observe(ws.conn,self.good,{'status':'completed'},SimpleNamespace(
                last_dialogue_observation={'requested_model':'good-model'}),20)
        selection = self.app.state()['model_selection']
        self.assertEqual(selection['recommended_model'],None)  # Synthetic old timestamp is stale.
        self.assertTrue(selection['current_blocked'])

    def test_legacy_400_is_only_a_matching_task_warning_and_manual_restore_clears_it(self):
        with self.app.workspace() as ws, ws.conn:
            result = {'model_error':{'code':'http_error','http_status':400}}
            ws.conn.execute("INSERT INTO web_runs(id,item_id,request_key,status,message,config,result) VALUES(?,?,?,?,?,?,?)",
                ('old','main','old','error','fixture',json.dumps(snapshot(self.cfg)),json.dumps(result)))
        self.assertEqual(self.app.state()['model_selection']['unavailable'][0]['code'],'request_rejected')
        other = self.app.create('Separate task')
        self.assertFalse(self.app.state(other)['model_selection']['current_blocked'])
        self.app.restore_model('main','stale-model')
        self.assertFalse(self.app.state()['model_selection']['current_blocked'])

    def test_manual_new_tasks_inherit_main_model_not_the_disabled_env_default(self):
        self.app.update('main',{'settings':{'model':'good-model','reasoning':'default','enabled':False}})
        task = self.app.create('New topic')
        state = self.app.state(task)
        self.assertEqual(state['configuration']['model'],'good-model')
        self.assertEqual(state['configuration']['thinking_mode'],'default')
        self.assertTrue(state['enabled'])  # Independent call switch is not copied.

    def wait(self, run):
        deadline = time.monotonic()+5
        while time.monotonic() < deadline:
            result = self.app.events(run,0)
            if result['run']['status'] not in ('queued','running','cancelling'):
                return result
            time.sleep(.02)
        self.fail('Offline worker did not finish')

    def test_actual_run_records_exhaustion_without_retry_and_survives_reload(self):
        client = Mock(config=self.cfg, model=self.cfg.model, mode='live')
        client.dialogue_step.side_effect = LLMError('safe',code='quota_exhausted')
        self.factory.return_value = client
        run = self.app.submit('main','offline quota fixture','exhausted')
        self.assertEqual(self.wait(run)['run']['status'],'error')
        self.assertTrue(self.app.state()['model_selection']['current_blocked'])
        self.assertEqual(self.app.submit('main','same request','exhausted'),run)
        with self.assertRaises(Conflict):
            self.app.submit('main','not retried','another-key')
        self.assertEqual(client.dialogue_step.call_count,1)
        self.assertEqual(self.factory.call_count,1)
        self.app.close()
        reopened = WebApp(self.cfg,llm_factory=self.factory)
        self.addCleanup(reopened.close)
        self.assertTrue(reopened.state()['model_selection']['current_blocked'])

    def test_successful_host_run_records_matching_model_observation(self):
        client = Mock(config=self.cfg, model=self.cfg.model, mode='live')
        client.last_dialogue_observation = {'requested_model':self.cfg.model,'returned_model':self.cfg.model}
        self.factory.return_value = client

        def gather(ws, llm, context, project_id, **callbacks):
            return {**context,'answer':'offline fixture answer','grounding':{},'dialogue_events':[]}

        with patch('lodestar.agent.dialogue.gather',side_effect=gather):
            run = self.app.submit('main','offline success fixture','working')
            self.assertEqual(self.wait(run)['run']['status'],'completed')
        self.assertEqual(self.app.state()['model_selection']['availability'][self.cfg.model],'recently_available')
        client.dialogue_step.assert_not_called()  # This is a host-observer fixture, not a live model claim.

    def test_http_restore_is_authenticated_and_new_module_is_served(self):
        self.record(self.cfg,'quota_exhausted')
        server = make_server(self.app,0)
        worker = threading.Thread(target=server.serve_forever,daemon=True)
        worker.start()
        client = requests.Session()
        client.trust_env = False
        url = f'http://127.0.0.1:{server.server_port}'
        try:
            data = {'model':'stale-model'}
            self.assertEqual(client.post(url+'/api/model-restore',json=data,timeout=3).status_code,403)
            self.assertTrue(self.app.state()['model_selection']['current_blocked'])
            self.record(self.good,'available')
            self.assertEqual(client.post(url+'/api/model-default',json={},timeout=3).status_code,403)
            headers = {'X-Lodestar-Token':self.app.token,'Origin':url}
            selected = client.post(url+'/api/model-default',json={},headers=headers,timeout=3)
            self.assertEqual(selected.status_code,200)
            self.assertEqual(selected.json(),{'changed':True,'model':'good-model'})
            self.assertEqual(client.post(url+'/api/model-restore',json=data,headers=headers,timeout=3).status_code,200)
            self.assertFalse(self.app.state()['model_selection']['current_blocked'])
            response = client.get(url+'/models.mjs',timeout=3)
            self.assertEqual(response.status_code,200)
            self.assertIn('text/javascript',response.headers['Content-Type'])
            self.factory.assert_not_called()
        finally:
            client.close()
            server.shutdown()
            worker.join(5)
            server.server_close()

    def test_paused_and_mock_modes_cannot_adopt_mock_success_for_live_default(self):
        self.record(replace(self.good,llm_mode='mock'),'available')
        self.assertIsNone(self.app.state()['model_selection']['recommended_model'])


if __name__ == '__main__':
    unittest.main()
