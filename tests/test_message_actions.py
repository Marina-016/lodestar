"""Real persistence/worker tests for deletion and append-only continuations."""
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from lodestar.agent.continuation import ContinuationText
from lodestar.agent.conversation import ConversationAgent, _history_context
from lodestar.config import Config
from lodestar.llm import LLMError
from lodestar.memory import repo
from lodestar.web import history
from lodestar.web.server import WebApp, Conflict


class ReplyLLM:
    text = '\n\n## 第二部分\n这是剩下的说明。'
    error = None
    calls = []
    def __init__(self, config): pass
    def close(self): pass
    def dialogue_step(self, system, messages, tools, on_text=None):
        type(self).calls.append((system,messages,tools))
        if on_text:
            for part in [self.text[:6],self.text[6:]]:
                on_text(part)
        if self.error:
            raise LLMError('synthetic failure',code=self.error)
        return {'role':'assistant','content':[{'type':'text','text':self.text}]}


class MessageActionsTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        root=Path(self.tmp.name)
        self.app=WebApp(Config(llm_mode='mock',search_mode='mock',model='offline-test',
            db_path=root/'web.db',workspace_dir=root),llm_factory=ReplyLLM)
        ReplyLLM.text,ReplyLLM.error,ReplyLLM.calls='\n\n## 第二部分\n这是剩下的说明。',None,[]

    def tearDown(self):
        self.app.close();self.tmp.cleanup()

    def seed(self, item='main', text='第一部分已经写好了。', kind='error'):
        with self.app.workspace() as ws:
            cid=self.app.item(ws,item)['conversation_id']
            user=repo.add_message(ws.conn,cid,'user','解释一下这个研究')
            answer=repo.add_message(ws.conn,cid,'assistant',text+'[旧错误文案]',kind=kind,
                metadata={'partial_answer':text,'model_error':{'code':'truncated'},
                          'source_details':[{'url':'https://example.org/paper','title':'原来的来源'}]})
            return user,answer

    def wait(self, run):
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            value=self.app.events(run,0)
            if value['run']['status'] not in ('queued','running','cancelling'):
                return value
            time.sleep(.01)
        self.fail('worker did not finish')

    def test_continue_preserves_id_prefix_sources_and_question_without_tools(self):
        user,answer=self.seed()
        run=self.app.continue_answer('main',answer['id'],'resume')
        result=self.wait(run)
        self.assertEqual(result['run']['status'],'completed')
        rows=self.app.state()['messages']
        self.assertEqual([r['id'] for r in rows],[user['id'],answer['id']])
        self.assertEqual(rows[-1]['content'],'第一部分已经写好了。'+ReplyLLM.text)
        self.assertEqual(rows[-1]['kind'],'text')
        self.assertNotIn('partial_answer',rows[-1]['metadata'])
        self.assertEqual(ReplyLLM.calls[0][2],[])
        self.assertNotIn('[旧错误文案]',json.dumps(ReplyLLM.calls,ensure_ascii=False))
        self.assertEqual(self.app.continue_answer('main',answer['id'],'resume'),run)
        with self.app.workspace() as ws:
            metadata=json.loads(ws.conn.execute('SELECT metadata FROM messages WHERE id=?',(answer['id'],)).fetchone()[0])
            self.assertEqual(metadata['source_details'][0]['title'],'原来的来源')
        with self.assertRaises(Conflict):self.app.continue_answer('main',answer['id'],'again')

    def test_failed_continuation_keeps_both_old_and_new_partial_and_can_resume(self):
        _,answer=self.seed(text='```python\nvalue = ')
        ReplyLLM.text='42\n```\n'
        ReplyLLM.error='timeout'
        result=self.wait(self.app.continue_answer('main',answer['id'],'first'))
        self.assertEqual(result['run']['status'],'error')
        row=self.app.state()['messages'][-1]
        self.assertEqual(row['metadata']['partial_answer'],'```python\nvalue = 42\n```\n')
        ReplyLLM.error=None;ReplyLLM.text='\n完成。'
        self.wait(self.app.continue_answer('main',answer['id'],'second'))
        rows=self.app.state()['messages']
        self.assertEqual(len(rows),2)
        self.assertEqual(rows[-1]['content'],'```python\nvalue = 42\n```\n\n完成。')

    def test_cancelled_continuation_keeps_streamed_text(self):
        _,answer=self.seed()
        ReplyLLM.error='cancelled'
        result=self.wait(self.app.continue_answer('main',answer['id'],'stop'))
        row=self.app.state()['messages'][-1]
        self.assertEqual(result['run']['status'],'cancelled')
        self.assertEqual(row['metadata']['partial_answer'],'第一部分已经写好了。'+ReplyLLM.text)

    def test_continue_rejects_old_empty_and_cross_item_replies(self):
        _,answer=self.seed(text='')
        with self.assertRaises(Conflict):self.app.continue_answer('main',answer['id'],'empty')
        other=self.app.create('另一个任务')
        with self.assertRaises(Conflict):self.app.continue_answer(other,answer['id'],'other')
        with self.app.workspace() as ws:
            cid=self.app.item(ws,'main')['conversation_id']
            repo.add_message(ws.conn,cid,'user','新的问题')
        with self.assertRaises(Conflict):self.app.continue_answer('main',answer['id'],'old')

    def test_delete_answer_scrubs_run_replay_but_keeps_question_and_ids(self):
        def gather(ws,llm,context,project_id,**callbacks):
            callbacks['on_text']('PRIVATE_ANSWER')
            return {**context,'answer':'PRIVATE_ANSWER','grounding':{},'dialogue_events':[]}
        with patch('lodestar.agent.dialogue.gather',side_effect=gather):
            run=self.app.submit('main','保留问题','delete-me');self.wait(run)
        rows=self.app.state()['messages'];answer=rows[-1]
        deleted=self.app.delete_message('main',answer['id'])
        self.assertEqual(deleted['deleted_ids'],[answer['id']])
        self.assertEqual([m['role'] for m in self.app.state()['messages']],['user'])
        self.assertNotIn('PRIVATE_ANSWER',json.dumps(self.app.events(run,0)))
        with self.app.workspace() as ws:
            cid=self.app.item(ws,'main')['conversation_id']
            self.assertEqual(ConversationAgent(ws,None).history(cid,self.app.user())[0]['content'],'保留问题')
            raw=ws.conn.execute('SELECT * FROM messages WHERE id=?',(answer['id'],)).fetchone()
            self.assertEqual(raw['content'],'');self.assertEqual(raw['kind'],'superseded')
            self.assertEqual(history.search(ws,'PRIVATE_ANSWER')['total'],0)

    def test_delete_user_removes_only_its_turn_and_preserves_saved_memory(self):
        user,answer=self.seed()
        other_user,other_answer=self.seed(text='另一轮')
        self.app.note('main',{'content':'独立保存的偏好','scope':'personal'})
        result=self.app.delete_message('main',user['id'])
        self.assertEqual(result['deleted_ids'],[user['id'],answer['id']])
        self.assertEqual([m['id'] for m in self.app.state()['messages']],[other_user['id'],other_answer['id']])
        self.assertTrue(self.app.state()['notes'])
        other=self.app.create('隔离空间')
        with self.assertRaises(LookupError):self.app.delete_message(other,other_answer['id'])

    def test_deletion_is_blocked_during_a_run(self):
        _,answer=self.seed()
        with self.app.workspace() as ws,ws.conn:
            ws.conn.execute("INSERT INTO web_runs(id,item_id,request_key,status,message,config) VALUES('busy','main','busy','running','q','{}')")
        with self.assertRaises(Conflict):self.app.delete_message('main',answer['id'])

    def test_deletion_after_continuing_scrubs_every_linked_run_and_keeps_receipts(self):
        _,answer=self.seed()
        with self.app.workspace() as ws,ws.conn:
            ws.conn.execute("INSERT INTO web_runs(id,item_id,request_key,status,message,config,result) VALUES('original','main','original','error','q','{}',?)",
                            (json.dumps({'answer':'PRIVATE_ORIGINAL'}),))
            ws.conn.execute("INSERT INTO web_events(run_id,kind,data) VALUES('original','text',?)",(json.dumps('PRIVATE_ORIGINAL'),))
            metadata=json.loads(ws.conn.execute('SELECT metadata FROM messages WHERE id=?',(answer['id'],)).fetchone()[0])
            metadata.update(run_id='original',action_receipts=[{'action':'save_memory','memory_id':'kept'}])
            ws.conn.execute('UPDATE messages SET metadata=? WHERE id=?',(json.dumps(metadata),answer['id']))
        resumed=self.app.continue_answer('main',answer['id'],'continue');self.wait(resumed)
        self.assertEqual(self.app.state()['messages'][-1]['metadata']['action_receipts'][0]['memory_id'],'kept')
        self.app.delete_message('main',answer['id'])
        for run in ['original',resumed]:
            result=self.app.events(run,0)
            self.assertEqual(result['events'],[])
            self.assertTrue(json.loads(result['run']['result'])['source_removed'])

    def test_restart_recovers_received_prose_once_without_a_model_call(self):
        with self.app.workspace() as ws,ws.conn:
            cid=self.app.item(ws,'main')['conversation_id']
            repo.add_message(ws.conn,cid,'user','重启测试')
            ws.conn.execute("INSERT INTO web_runs(id,item_id,request_key,status,message,config) VALUES('restart','main','restart','running','重启测试','{}')")
            for chunk in ['已经写下的','167 字会保留。']:
                ws.conn.execute("INSERT INTO web_events(run_id,kind,data) VALUES('restart','text',?)",(json.dumps(chunk),))
        reopened=WebApp(self.app.config,llm_factory=ReplyLLM)
        try:
            rows=reopened.state()['messages']
            self.assertEqual(len(rows),2)
            self.assertEqual(rows[-1]['metadata']['partial_answer'],'已经写下的167 字会保留。')
            self.assertEqual(reopened.state()['run']['status'],'error')
            from lodestar.web.recovery import recover_latest_interrupted
            with reopened.workspace() as ws:self.assertEqual(recover_latest_interrupted(ws.conn),[])
            self.assertEqual(ReplyLLM.calls,[])
        finally:reopened.close()

    def test_restart_of_a_continuation_does_not_repeat_committed_text(self):
        _,answer=self.seed()
        run=self.app.continue_answer('main',answer['id'],'commit');self.wait(run)
        before=self.app.state()['messages'][-1]['content']
        with self.app.workspace() as ws,ws.conn:
            ws.conn.execute("UPDATE web_runs SET status='interrupted' WHERE id=?",(run,))
            from lodestar.web.recovery import recover_latest_interrupted
            recover_latest_interrupted(ws.conn)
        self.assertEqual(self.app.state()['messages'][-1]['content'],before)
        self.assertEqual(self.app.state()['run']['status'],'completed')


class ContinuationTextTest(unittest.TestCase):
    def test_preface_removed_and_markdown_join_preserved(self):
        received=[];output=ContinuationText('前文。',received.append)
        for part in ['好的，接下来我','继续。\n','\n## 细节\n正文']:
            output.feed(part)
        output.finish()
        self.assertEqual(output.text,'\n## 细节\n正文')
        self.assertEqual(''.join(received),output.text)
        self.assertEqual(_history_context([{'role':'assistant','kind':'error','content':'不应当带入的报错','metadata':{}}]),[])

    def test_exact_repeated_tail_removed_without_fuzzy_deletion(self):
        tail='这是一段长度足够的已经生成的完整说明。'
        output=ContinuationText('更早的正文。'+tail)
        output.feed(tail+'\n接下来是新内容。');output.finish()
        self.assertEqual(output.text,'\n接下来是新内容。')


if __name__=='__main__':unittest.main()
