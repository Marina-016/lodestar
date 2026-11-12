"""Synthetic public-topic acceptance; no chat DB, personal memory or chat writes.

--live uses the configured DianDian endpoint and actual bounded public searches.
--required probes the required tool-choice transport with one model request only.
"""
import argparse
from dataclasses import replace
import json
import sys
from types import SimpleNamespace
from lodestar.config import load_config
from lodestar.chat_settings import configure
from lodestar.llm import LLMClient, LLMError, error_details
from lodestar.agent.dialogue import gather, SYSTEM, tool_definitions


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--required',action='store_true')
    args=parser.parse_args()
    cfg=configure(load_config(),model='dots3-note-prev',thinking='off')
    endpoint='https://note3-prev-api.askdiandian.com/v1'
    if cfg.llm_provider!='diandian' or cfg.llm_base_url.rstrip('/')!=endpoint:
        raise SystemExit('This acceptance only targets the configured DianDian /v1 endpoint')
    if cfg.model_calls_disabled:raise SystemExit('Model calls are paused')
    cfg=replace(cfg,max_tokens=900,conversation_timeout_s=45,dialogue_max_operations=3,tool_timeout_s=30,enrich_venues=False)
    context={'message':'去搜呀','history':[
        {'role':'user','content':'我想了解全双工语音助手边听边说的研究，请找相关论文。'},
        {'role':'assistant','content':'我先搜一下这个方向的文献。'}],
        'papers':[],'candidates':[],'managed_memory':[],'learning':[]}
    if not args.live:
        print(json.dumps({'synthetic_only':True,'private_chat_loaded':False,'endpoint':endpoint}));return
    client=LLMClient(cfg)
    try:
        if args.required:
            tools=[t for t in tool_definitions(None) if t['name']=='search_papers']
            reply=client.dialogue_step(SYSTEM,[{'role':'user','content':'请检索全双工语音助手研究论文，用英文主题词搜索。'}],tools,tool_choice='required')
            print(json.dumps({'synthetic_only':True,'required_tool_calls':[b.get('name') for b in reply['content'] if b['type']=='tool_use']},ensure_ascii=False),flush=True)
        else:
            result=gather(SimpleNamespace(config=cfg,conn=None),client,context,
                on_progress=lambda action:print(json.dumps({'phase':action}),flush=True))
            print(json.dumps({'synthetic_only':True,'tool_dispatches':result['grounding']['tool_dispatches'],
                'actions':[{'name':e['action'],'query':e['params'].get('query') or e['params'].get('queries'),
                    'error':bool(e['result'].get('error'))} for e in result['dialogue_events']],
                'answer':result['answer']},ensure_ascii=False),flush=True)
    except LLMError as error:
        print(json.dumps({'synthetic_only':True,'error':error_details(error)},ensure_ascii=False),flush=True)
        raise SystemExit(1)
    finally:client.close()


if __name__=='__main__':main()
