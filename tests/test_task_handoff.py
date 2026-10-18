"""Task handoff must disclose run identity, excerpt coverage and source provenance."""
import json
import unittest
from unittest.mock import Mock, patch

from lodestar.agent.dialogue import gather
from lodestar.memory import repo
from tests import test_task_wait


def response(text='Scripted summary'):
    return {'role':'assistant','content':[{'type':'text','text':text}]}


def calls(name,args):
    return {'role':'assistant','content':[{'type':'tool_use','id':name,'name':name,'input':args}]}


class TaskHandoffTests(unittest.TestCase):
    setUp = test_task_wait.TaskWaitTests.setUp
    tearDown = test_task_wait.TaskWaitTests.tearDown
    run_row = test_task_wait.TaskWaitTests.run_row

    def message(self,text,*,role='assistant',run_id=None,kind='text',sources=None):
        with self.app.workspace() as ws:
            return repo.add_message(ws.conn,self.child['conversation_id'],role,text,kind=kind,
                metadata={**({'run_id':run_id} if run_id else {}),'source_details':sources or []})

    def test_old_success_is_not_associated_with_new_running_task(self):
        self.run_row('completed','old-run')
        old=self.message('OLD_SUCCESS',run_id='old-run')
        self.run_row('running','new-run')
        current=self.message('NEW_REQUEST',role='user',run_id='new-run')
        value=self.read({'id':self.task_id,'wait_seconds':0})
        self.assertEqual(value['run_id'],'new-run')
        self.assertEqual(value['run_status'],'running')
        self.assertEqual(value['messages'][0]['run_id'],'old-run')
        self.assertEqual(value['messages'][0]['run_link'],'earlier_run')
        self.assertEqual(value['messages'][1]['run_link'],'current_run')
        self.assertEqual(value['transcript']['current_run_message_ids'],[current['id']])
        self.assertEqual(value['transcript']['current_run_assistant_message_ids'],[])
        self.assertEqual(value['messages'][0]['id'],old['id'])

    def test_error_run_message_keeps_error_kind_and_host_status(self):
        self.run_row('completed','old-run')
        self.message('OLD_SUCCESS',run_id='old-run')
        self.run_row('error','new-run')
        failed=self.message('NEW_FAILURE',run_id='new-run',kind='error')
        value=self.read({'id':self.task_id,'wait_seconds':0})
        latest=value['messages'][-1]
        self.assertEqual(latest['kind'],'error')
        self.assertEqual(latest['run_status'],'error')
        self.assertEqual(latest['run_link'],'current_run')
        self.assertEqual(value['transcript']['current_run_assistant_message_ids'],[failed['id']])
        self.assertEqual(value['run_status'],'error')

    def test_legacy_and_wrong_task_run_links_remain_unlinked(self):
        self.run_row('completed')
        foreign=self.app.create('Other task')
        with self.app.workspace() as ws,ws.conn:
            ws.conn.execute('INSERT INTO web_runs(id,item_id,request_key,status,message,config) VALUES(?,?,?,?,?,?)',
                            ('foreign-run',foreign,'foreign','cancelled','FOREIGN_RUN_CANARY','{}'))
        self.message('LEGACY_WITHOUT_LINK')
        self.message('UNRESOLVED_LINK',run_id='foreign-run')
        with self.app.workspace() as ws:
            repo.add_message(ws.conn,self.child['conversation_id'],'assistant','NONSTRING_LEGACY_LINK',
                             metadata={'run_id':['invalid-link']})
        value=self.read({'id':self.task_id,'wait_seconds':0})
        self.assertTrue(all(m['run_link']=='unlinked' and m['run_id'] is None for m in value['messages']))
        self.assertEqual(value['transcript']['current_run_assistant_message_ids'],[])
        self.assertNotIn('FOREIGN_RUN_CANARY',json.dumps(value))

    def test_long_result_retains_tail_caveat_and_reports_message_and_transcript_limits(self):
        self.run_row('completed')
        retired=self.message('RETIRED_CANARY')
        with self.app.workspace() as ws,ws.conn:
            ws.conn.execute('UPDATE web_items SET context_after=? WHERE id=?',(retired['id'],self.task_id))
        for index in range(7):
            self.message(f'Visible {index}')
        original='BEGIN_MECHANISM '+ 'x'*13000+' TAIL_FINAL_CAVEAT'
        last=self.message(original,run_id='child-run')
        value=self.read({'id':self.task_id,'wait_seconds':0})
        excerpt=value['messages'][-1]
        self.assertIn('BEGIN_MECHANISM',excerpt['content'])
        self.assertIn('TAIL_FINAL_CAVEAT',excerpt['content'])
        self.assertLessEqual(len(excerpt['content']),12000)
        self.assertTrue(excerpt['content_truncated'])
        self.assertEqual(excerpt['original_content_chars'],len(original))
        self.assertEqual(excerpt['content_coverage'],'head_and_tail_excerpt')
        self.assertEqual(value['transcript']['visible_messages'],8)
        self.assertEqual(value['transcript']['returned_messages'],6)
        self.assertTrue(value['transcript']['messages_truncated'])
        self.assertEqual(value['transcript']['truncated_message_ids'],[last['id']])
        self.assertNotIn('RETIRED_CANARY',json.dumps(value))

    def test_forwarded_source_metadata_does_not_claim_parent_read_body(self):
        url='https://arxiv.org/abs/2601.00001v1'
        self.run_row('completed')
        child=self.message('Child summary only',run_id='child-run',sources=[{'url':url,'source_type':'paper','read_depth':'full'}])
        for parent_body in (False,True):
            with self.subTest(parent_body=parent_body):
                llm=Mock()
                llm.dialogue_step.side_effect=[calls('read_task',{'id':self.task_id,'wait_seconds':0}),response()]
                context={'message':'Summarize task','papers':([{'url':url,'source_type':'paper',
                    'read_depth':'full','content':'PARENT_BODY_ALREADY_READ'}] if parent_body else [])}
                with self.app.workspace() as ws:
                    result=gather(ws,llm,context,max_calls=1,extra_tools=self.tools)
                packet=json.loads(llm.dialogue_step.call_args.args[1][0]['content'].split('\n',1)[1].split('\n\nUser request:\n',1)[0])
                source=packet['sources'][0]
                self.assertEqual(source['read_depth'],'full' if parent_body else 'metadata')
                self.assertEqual(source['body_observation'],'bounded_excerpt_read' if parent_body else 'not_read')
                self.assertEqual(source['linked_task_source']['reported_read_depth'],'full')
                self.assertEqual(source['linked_task_source']['message_id'],child['id'])
                if parent_body:
                    self.assertIn('PARENT_BODY_ALREADY_READ',json.dumps(packet))
                else:
                    self.assertEqual(result['grounding']['source_depths'][url],'metadata')

    def test_parent_can_choose_to_read_forwarded_source_and_obtain_actual_body(self):
        url='https://arxiv.org/abs/2601.00001v1'
        self.run_row('completed')
        self.message('Child summary',run_id='child-run',sources=[{'url':url,'source_type':'paper','read_depth':'full'}])
        llm=Mock()
        llm.dialogue_step.side_effect=[calls('read_task',{'id':self.task_id,'wait_seconds':0}),
            calls('read_paper',{'url':url,'full_text':True}),response()]
        with self.app.workspace() as ws,patch('lodestar.tools.registry.call_tool',return_value={
                'url':url,'text':'ACTUAL_PARENT_BODY','read_depth':'full','full_text_ok':True}) as read:
            ws.config.enrich_venues=False
            result=gather(ws,llm,{'message':'Read evidence','papers':[]},max_calls=2,extra_tools=self.tools)
        self.assertEqual(read.call_count,1)
        self.assertEqual(result['grounding']['source_depths'][url],'full')
        self.assertIn('ACTUAL_PARENT_BODY',result['papers'][0]['content'])

    def test_forwarding_web_metadata_does_not_downgrade_existing_parent_body(self):
        url='https://example.invalid/article'
        self.run_row('completed')
        self.message('Child webpage summary',run_id='child-run',sources=[{'url':url,'source_type':'web','read_depth':'web'}])
        llm=Mock()
        llm.dialogue_step.side_effect=[calls('read_task',{'id':self.task_id,'wait_seconds':0}),response()]
        with self.app.workspace() as ws:
            result=gather(ws,llm,{'message':'Summarize','papers':[{'url':url,'source_type':'web',
                'read_depth':'web','content':'PARENT_WEB_BODY'}]},max_calls=1,extra_tools=self.tools)
        self.assertEqual(result['grounding']['source_depths'][url],'web')
        self.assertIn('PARENT_WEB_BODY',llm.dialogue_step.call_args.args[1][0]['content'])
