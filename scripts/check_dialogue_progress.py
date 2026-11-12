"""Generated fixtures only; never loads a chat database or user memory.

--live makes one request per synthetic case to the already configured DianDian
endpoint. No tool execution, chat writes, or automatic retries.
"""
import argparse
from copy import deepcopy
from dataclasses import replace
import json
import sys
from types import SimpleNamespace
from unittest.mock import patch

from lodestar.agent.dialogue import gather
from lodestar.chat_settings import configure
from lodestar.config import load_config
from lodestar.llm import LLMClient,LLMError,error_details


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--live',action='store_true')
    parser.add_argument('--case',choices=['one_pending_step','one_offer_with_escape','two_open_choices','nothing_pending'])
    args=parser.parse_args()
    endpoint='https://note3-prev-api.askdiandian.com/v1'
    cfg=configure(load_config(),model='dots3-note-prev',thinking='off')
    if cfg.llm_provider!='diandian' or cfg.llm_base_url.rstrip('/')!=endpoint:
        raise SystemExit('This test only targets the configured DianDian /v1 endpoint')
    if cfg.model_calls_disabled:raise SystemExit('Model calls are paused')
    cfg=replace(cfg,max_tokens=600,conversation_timeout_s=45)
    fixtures=[
        ('one_pending_step','请分步讲一个虚构的机器学习入门课程，一次一小段。',
         '第一部分的样本设计已经讲完。接下来只讲误差分析里的偏差和方差，可以吗？'),
        ('two_open_choices','我们学习一个虚构课程，每次选一个概念。',
         '这一部分结束了。下一步你想先看损失函数，还是优化器？'),
        ('one_offer_with_escape','请分步讲一个虚构的机器学习入门课程，一次一小段。',
         '实验设计这部分已经讲完。懂了的话，我接着讲误差分析里的偏差和方差？还是你想先问别的？'),
        ('nothing_pending','这个术语的定义我理解了，谢谢。',
         '嗯，这个概念先讲到这里。'),
    ]
    for name,previous,ending in fixtures:
        if args.case and name!=args.case:continue
        context={'message':'ok','history':[{'role':'user','content':previous},
            {'role':'assistant','content':ending}], 'papers':[],'candidates':[],'managed_memory':[],'learning':[]}
        request={}
        class Recorder:
            model=cfg.model
            def dialogue_step(self,system,messages,tools,on_text=None):
                request.update(system=system,messages=deepcopy(messages),tools=tools)
                return {'role':'assistant','content':[{'type':'text','text':'captured'}]}
        with patch('lodestar.agent.dialogue._execute',side_effect=RuntimeError('Synthetic test cannot execute tools')):
            gather(SimpleNamespace(config=cfg),Recorder(),context,max_calls=1)
        print(json.dumps({'case':name,'endpoint':endpoint,'synthetic_only':True,
            'native_history_roles':[m['role'] for m in request['messages']]},ensure_ascii=False),flush=True)
        if not args.live:continue
        client=LLMClient(cfg);chunks=[]
        try:
            reply=client.dialogue_step(request['system'],request['messages'],request['tools'],on_text=chunks.append)
            print(json.dumps({'case':name,'answer':''.join(b['text'] for b in reply['content'] if b['type']=='text'),
                'tool_requests':sum(b['type']=='tool_use' for b in reply['content'])},ensure_ascii=False),flush=True)
        except LLMError as error:
            print(json.dumps({'case':name,'model_error':error_details(error),'partial_answer':''.join(chunks)},ensure_ascii=False),flush=True)
        finally:client.close()


if __name__=='__main__':main()
