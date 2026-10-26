"""Web adapter regression tests: persistence, isolation, runs and HTTP boundaries."""
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
import requests

from lodestar.agent.conversation import ConversationAgent
from lodestar.config import Config
from lodestar.memory import repo
from lodestar.llm import LLMError
from lodestar.web.server import Conflict, WebApp, make_server


class FakeLLM:
    def __init__(self, config):
        self.config = config

    def close(self):
        pass


class WebTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.config = Config(llm_mode='mock', search_mode='mock', model='offline-test',
                             db_path=root/'web.db', workspace_dir=root)
        self.app = WebApp(self.config,llm_factory=FakeLLM)

    def tearDown(self):
        # A terminal run is visible before its worker closes the DB/client.
        # Join that owned work before deleting the fixture directory on Windows.
        self.app.close()
        self.tmp.cleanup()

    def wait(self, run):
        deadline = time.monotonic()+5
        while time.monotonic()<deadline:
            value = self.app.events(run,0)
            if value['run']['status'] not in ('queued','running','cancelling'):
                return value
            time.sleep(.02)
        self.fail('run did not finish')

    @staticmethod
    def gather(ws,llm,context,project_id,**callbacks):
        callbacks['on_progress']('search_papers')
        text = '测试回答：' + context['message']
        callbacks['on_text'](text)
        return {**context,'answer':text,'grounding':{},'dialogue_events':[]}

    def test_boot_never_constructs_model_and_main_persists(self):
        original = self.app.state()['item']['conversation_id']
        with patch('lodestar.web.server.LLMClient',side_effect=AssertionError('hidden model call')):
            reopened = WebApp(self.config,llm_factory=FakeLLM)
            self.assertEqual(reopened.state()['item']['conversation_id'],original)
        self.assertEqual(reopened.state()['messages'],[])

    def test_unexpected_failure_keeps_safe_answer_after_reload(self):
        with patch('lodestar.agent.dialogue.gather', side_effect=RuntimeError('PRIVATE_PROVIDER_BODY')):
            result = self.wait(self.app.submit('main', 'Question', 'unexpected-error'))
        self.assertEqual(result['run']['status'], 'error')
        reopened = WebApp(self.config, llm_factory=FakeLLM)
        self.assertEqual(reopened.state()['messages'][-1]['role'], 'assistant')
        self.assertIn('本轮未完成', reopened.state()['messages'][-1]['content'])
        self.assertNotIn('PRIVATE_PROVIDER_BODY', json.dumps(reopened.state()))

    def test_actual_agent_context_is_scoped_to_task_and_enabled_notes(self):
        a,b = self.app.create('A'),self.app.create('B')
        self.app.note(a,{'content':'CANARY_PRIVATE_A', 'scope':'task'})
        self.app.note(b,{'content':'只在 B 中举例', 'scope':'task'})
        note = next(n for n in self.app.state(b)['notes'] if n['item_id'] == b)
        self.app.note(b,{'id':note['id'],'status':'disabled'})
        captured=[]
        def capture(ws,llm,context,project_id,**callbacks):
            captured.append(context)
            return self.gather(ws,llm,context,project_id,**callbacks)
        with patch('lodestar.agent.dialogue.gather',side_effect=capture):
            self.wait(self.app.submit(a,'A 的消息','a1'))
            run=self.wait(self.app.submit(b,'B 的消息','b1'))
        self.assertEqual(run['run']['status'],'completed')
        self.assertEqual([json.loads(r)['content'] for r in captured[0]['managed_memory']],['CANARY_PRIVATE_A'])
        self.assertEqual(captured[1]['managed_memory'],[])
        self.assertNotIn('CANARY_PRIVATE_A',json.dumps(captured[1],ensure_ascii=False))
        self.assertNotIn('A 的消息',json.dumps(captured[1],ensure_ascii=False))
        self.assertEqual(self.app.state('main')['messages'],[])
        self.assertEqual(self.app.state(b)['learning'],[])

    def test_request_key_deduplicates_and_events_recover(self):
        with patch('lodestar.agent.dialogue.gather',side_effect=self.gather):
            run=self.app.submit('main','测试问题','same-key')
            result=self.wait(run)
            self.assertEqual(self.app.submit('main','测试问题','same-key'),run)
        self.assertEqual([e['kind'] for e in result['events']],['progress','text'])
        self.assertEqual(self.app.events(run,result['events'][-1]['id'])['events'],[])
        self.assertEqual(len(self.app.state()['messages']),2)

    def test_per_task_settings_and_custom_profile(self):
        a=self.app.create('A')
        self.app.update(a,{'settings':{'model':'another-model','profile':'custom','thinking':'off','enabled':False}})
        self.assertEqual(self.app.state(a)['configuration']['model'],'another-model')
        self.assertEqual(self.app.state()['configuration']['model'],'offline-test')
        self.assertFalse(self.app.state(a)['enabled'])

    def test_native_reasoning_tracks_model_and_preserves_budget(self):
        from dataclasses import replace
        app = WebApp(replace(self.config, llm_provider='dashscope', model='qwen3.8-flash'), llm_factory=FakeLLM)
        app.update('main', {'settings': {'model': 'qwen3.8-flash', 'reasoning': 'medium', 'enabled': False}})
        before = app.state()
        self.assertEqual(before['configuration']['reasoning_effort'], 'medium')
        self.assertIsNone(before['configuration']['thinking_budget'])
        self.assertEqual(before['reasoning']['value'], 'medium')
        app.update('main', {'settings': {'model': 'deepseek-v4-pro', 'reasoning': 'max'}})
        after = app.state()
        self.assertEqual(after['reasoning']['value'], 'max')
        self.assertEqual(after['configuration']['operation_budget'], before['configuration']['operation_budget'])
        self.assertFalse(after['enabled'])
        with self.assertRaises(ValueError):
            app.update('main', {'settings': {'model': 'deepseek-v4-pro', 'reasoning': 'medium'}})
        self.assertEqual(app.state()['reasoning']['value'], 'max')

    def test_settings_saved_during_run_do_not_mutate_frozen_config(self):
        from dataclasses import replace
        app = WebApp(replace(self.config, llm_provider='dashscope', model='qwen3.8-flash'), llm_factory=FakeLLM)
        ready, release = threading.Event(), threading.Event()
        captured = []
        def gather(ws, llm, context, project_id, **callbacks):
            captured.append(llm.config)
            ready.set()
            release.wait(3)
            return self.gather(ws, llm, context, project_id, **callbacks)
        with patch('lodestar.agent.dialogue.gather', side_effect=gather):
            run = app.submit('main', '运行时修改设置', 'settings-during-run')
            try:
                self.assertTrue(ready.wait(3))
                app.update('main', {'settings': {'model': 'deepseek-v4-pro', 'reasoning': 'max'}})
                self.assertEqual(app.state()['configuration']['model'], 'deepseek-v4-pro')
                self.assertEqual(captured[0].model, 'qwen3.8-flash')
            finally:
                release.set()
            deadline = time.monotonic() + 4
            while app.events(run, 0)['run']['status'] in ('queued', 'running') and time.monotonic() < deadline:
                time.sleep(.02)
        self.assertEqual(json.loads(app.events(run, 0)['run']['config'])['model'], 'qwen3.8-flash')

    def test_catalog_is_explicit_cached_and_does_not_enable_inference(self):
        from unittest.mock import Mock
        from dataclasses import replace
        client = Mock()
        client.list_models.return_value = ['qwen3.8-flash', 'deepseek-v4-pro']
        factory = Mock(return_value=client)
        app = WebApp(replace(self.config, model_calls_disabled=True), llm_factory=factory)
        app.state()
        factory.assert_not_called()
        catalog = app.models('main')
        self.assertIn('deepseek-v4-pro', catalog['models'])
        self.assertEqual(app.models('main'), catalog)
        client.list_models.assert_called_once()
        client.complete.assert_not_called()
        self.assertFalse(app.state()['enabled'])

    def test_note_edit_disable_delete_and_shared_owner(self):
        a,b=self.app.create('A'),self.app.create('B')
        self.app.note(a,{'content':'原内容'})
        note=self.app.state(a)['notes'][0]
        self.app.note(b,{'id':note['id'],'content':'共享修改'})
        self.app.note(a,{'id':note['id'],'content':'新内容','status':'disabled'})
        updated=self.app.state(a)['notes'][0]
        self.assertEqual((updated['content'],updated['revision'],updated['status']),('新内容',3,'disabled'))
        self.app.note(a,{'id':note['id'],'status':'deleted'})
        self.assertEqual(self.app.state(a)['notes'],[])
        self.assertEqual(self.app.state(b)['notes'],[])

    def test_same_task_busy_and_restart_interrupted_without_retry(self):
        with self.app.workspace() as ws, ws.conn:
            ws.conn.execute("INSERT INTO web_runs(id,item_id,request_key,status,message,config) VALUES('old','main','key','running','a','{}')")
        with self.assertRaises(Conflict):
            self.app.submit('main','重复运行','new')
        reopened=WebApp(self.config,llm_factory=FakeLLM)
        self.assertEqual(reopened.state()['run']['status'],'interrupted')
        self.assertEqual(reopened.state()['messages'],[])

    def test_http_host_csrf_and_safe_static_assets(self):
        server=make_server(self.app,0)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        url=f'http://127.0.0.1:{server.server_port}'
        client=requests.Session();client.trust_env=False
        try:
            response=client.get(url,timeout=3)
            self.assertEqual(response.status_code,200)
            self.assertIn(self.app.token,response.text)
            self.assertIn("frame-ancestors 'none'",response.headers['Content-Security-Policy'])
            self.assertEqual(client.get(url+'/api/state',headers={'Host':'evil.test'},timeout=3).status_code,403)
            self.assertEqual(client.post(url+'/api/items',json={'name':'A'},timeout=3).status_code,403)
            self.assertEqual(client.post(url+'/api/items',json={'name':'A'},headers={'X-Lodestar-Token':self.app.token,'Origin':'https://evil.test'},timeout=3).status_code,403)
            good=client.post(url+'/api/items',json={'name':'A'},headers={'X-Lodestar-Token':self.app.token},timeout=3)
            self.assertEqual(good.status_code,201)
            self.assertEqual(client.get(url+'/style.css',timeout=3).status_code,200)
            actions = client.get(url+'/actions.mjs',timeout=3)
            self.assertEqual(actions.status_code,200)
            self.assertIn('javascript',actions.headers['Content-Type'])
        finally:
            client.close();server.shutdown();server.server_close();worker.join()

    def test_stop_stream_preserves_partial_answer_and_blocks_overlap(self):
        ready, release = threading.Event(), threading.Event()
        client = Mock()
        def step(system, messages, tools, on_text):
            on_text('Delivered before stop')
            ready.set()
            release.wait(3)
            on_text('LATE TEXT MUST NOT ARRIVE')
        client.dialogue_step.side_effect = step
        self.app.llm_factory = Mock(return_value=client)
        with patch('lodestar.tools.registry.call_tool') as tool:
            run = self.app.submit('main', 'Stream a response', 'stop-stream')
            try:
                self.assertTrue(ready.wait(3))
                self.assertEqual(self.app.cancel('main',run), 'cancelling')
                self.assertEqual(self.app.cancel('main',run), 'cancelling')
                with self.assertRaises(Conflict):
                    self.app.submit('main','Overlap','overlap')
                with self.assertRaises(Conflict):
                    self.app.note('main',{'content':'Change context while stopping'})
            finally:
                release.set()
            result = self.wait(run)
        self.assertEqual(result['run']['status'],'cancelled')
        messages = self.app.state()['messages']
        self.assertEqual(len(messages),2)
        self.assertEqual(messages[-1]['kind'],'cancelled')
        self.assertIn('Delivered before stop',messages[-1]['content'])
        self.assertNotIn('LATE TEXT',messages[-1]['content'])
        self.assertEqual([e['data'] for e in result['events'] if e['kind']=='text'],['Delivered before stop'])
        client.complete_json.assert_not_called()
        tool.assert_not_called()
        with self.app.workspace() as ws:
            with self.assertRaises(LLMError):
                self.app.event(ws,run,'text','late callback')
        with patch('lodestar.agent.dialogue.gather',side_effect=self.gather):
            next_run = self.app.submit('main','Continue after stop','after-stop')
            self.assertEqual(self.wait(next_run)['run']['status'],'completed')
        self.assertEqual(self.app.cancel('main',run),'cancelled')
        self.assertEqual(self.app.cancel('main',next_run),'completed')

    def test_stop_waiting_request_is_cancelled_even_if_transport_times_out(self):
        ready, release = threading.Event(), threading.Event()
        client = Mock()
        def step(*args, **kwargs):
            ready.set()
            release.wait(3)
            raise LLMError('Waiting request timed out',code='timeout')
        client.dialogue_step.side_effect = step
        self.app.llm_factory = Mock(return_value=client)
        run = self.app.submit('main','Wait for a response','stop-timeout')
        try:
            self.assertTrue(ready.wait(3))
            self.app.cancel('main',run)
        finally:
            release.set()
        self.assertEqual(self.wait(run)['run']['status'],'cancelled')
        message = self.app.state()['messages'][-1]
        self.assertEqual(message['kind'],'cancelled')
        self.assertEqual(message['metadata']['model_error']['code'],'cancelled')
        client.dialogue_step.assert_called_once()

    def test_stop_queued_run_never_constructs_provider(self):
        factory = self.app.llm_factory = Mock()
        with patch('lodestar.web.server.threading.Thread') as worker:
            run = self.app.submit('main','Queued question','stop-queued')
            args = worker.call_args.kwargs['args']
        self.assertEqual(self.app.cancel('main',run),'cancelling')
        self.app.execute(*args)
        self.assertEqual(self.app.events(run,0)['run']['status'],'cancelled')
        factory.assert_not_called()
        self.assertEqual([m['role'] for m in self.app.state()['messages']],['user','assistant'])

    def test_stop_during_tool_retains_observation_without_another_model_call(self):
        ready, release = threading.Event(), threading.Event()
        client = Mock()
        client.dialogue_step.return_value = {'role':'assistant','content':[{'type':'tool_use',
            'id':'search','name':'search_web','input':{'query':'topic'}}]}
        self.app.llm_factory = Mock(return_value=client)
        source = {'url':'https://official.example/article','title':'Observed during stop'}
        def search(*args):
            ready.set()
            release.wait(3)
            return {'sources':[source]}
        with patch('lodestar.tools.registry.call_tool',side_effect=search) as tool:
            run = self.app.submit('main','Search a topic','stop-tool')
            try:
                self.assertTrue(ready.wait(3))
                self.app.cancel('main',run)
                other = self.app.create('Other task')
                with self.assertRaises(LookupError):
                    self.app.cancel(other,run)
            finally:
                release.set()
            self.wait(run)
        client.dialogue_step.assert_called_once()
        tool.assert_called_once()
        meta = self.app.state()['messages'][-1]['metadata']
        self.assertEqual(meta['candidates'][0]['url'],source['url'])
        self.assertEqual(meta['dialogue_events'][0]['result']['sources'][0],source)

    def test_stopping_run_is_recovered_as_interrupted_without_inference(self):
        with self.app.workspace() as ws, ws.conn:
            ws.conn.execute("INSERT INTO web_runs(id,item_id,request_key,status,message,config) VALUES('stopping','main','stop','cancelling','input','{}')")
        factory = Mock()
        reopened = WebApp(self.config,llm_factory=factory)
        self.assertEqual(reopened.state()['run']['status'],'interrupted')
        factory.assert_not_called()

    def test_cancel_http_requires_same_origin_token_and_correct_item(self):
        with self.app.workspace() as ws, ws.conn:
            ws.conn.execute("INSERT INTO web_runs(id,item_id,request_key,status,message,config) VALUES('queued','main','key','queued','input','{}')")
        other = self.app.create('Other')
        server = make_server(self.app,0)
        worker = threading.Thread(target=server.serve_forever,daemon=True)
        worker.start()
        client = requests.Session(); client.trust_env=False
        url = f'http://127.0.0.1:{server.server_port}'
        try:
            data = {'item':'main','run':'queued'}
            self.assertEqual(client.post(url+'/api/cancel',json=data,timeout=3).status_code,403)
            headers = {'X-Lodestar-Token':self.app.token}
            self.assertEqual(client.post(url+'/api/cancel',json={**data,'item':other},headers=headers,timeout=3).status_code,404)
            response = client.post(url+'/api/cancel',json=data,headers=headers,timeout=3)
            self.assertEqual(response.json()['status'],'cancelling')
            self.assertEqual(client.get(url+'/sources.mjs',timeout=3).status_code,200)
        finally:
            client.close(); server.shutdown(); server.server_close(); worker.join()

    def test_live_disabled_stops_before_model_call(self):
        from dataclasses import replace
        app=WebApp(replace(self.config,llm_mode='live',model_calls_disabled=True),llm_factory=FakeLLM)
        with self.assertRaises(Conflict):
            app.submit('main','不能调用','disabled')


if __name__=='__main__':
    unittest.main()
