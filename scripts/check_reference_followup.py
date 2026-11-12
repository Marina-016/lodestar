"""Read-only replay of an owned dialogue turn; --live enables one model call.

No chat writes, tool execution, automatic retry, or saved prompt/history dumps.
"""
import argparse
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace
from unittest.mock import patch

from lodestar.agent.conversation import ConversationAgent
from lodestar.agent.dialogue import gather
from lodestar.chat_settings import configure
from lodestar.config import load_config
from lodestar.llm import LLMClient
from lodestar.providers.capabilities import apply_reasoning
from lodestar.web import memory
from lodestar.memory.assistant import select_rows,prompt_rows,reconstruct_context


class Captured(Exception):pass


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--message',type=int,required=True)
    parser.add_argument('--live',action='store_true')
    args=parser.parse_args()
    path=Path('workspace/web/web.db').resolve()
    conn=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True);conn.row_factory=sqlite3.Row
    try:
        row=conn.execute("SELECT m.* FROM messages m JOIN web_items i ON i.conversation_id=m.conversation_id WHERE m.id=? AND m.role='user' AND i.owner='local'",(args.message,)).fetchone()
        if not row:raise SystemExit('Owned user message not found')
        item=dict(conn.execute("SELECT * FROM web_items WHERE conversation_id=? AND owner='local'",(row['conversation_id'],)).fetchone())
        metadata=json.loads(row['metadata'] or '{}');reference=metadata.get('reference_text','')
        settings=json.loads(item['settings']);allowed={k:v for k,v in settings.items() if k in {'model','profile','thinking'}}
        if allowed.get('profile')=='custom':allowed.pop('profile')
        cfg=configure(load_config(),**allowed)
        if 'reasoning' in settings:cfg=apply_reasoning(cfg,settings['reasoning'])
        if not settings.get('enabled',True) or cfg.model_calls_disabled:raise SystemExit('Model calls are paused')
        cfg=replace(cfg,max_tokens=800,conversation_timeout_s=45)
        user=conn.execute('SELECT user_id FROM agent_sessions WHERE conversation_id=?',(row['conversation_id'],)).fetchone()[0]
        ws=SimpleNamespace(conn=conn,config=cfg)
        query=row['content']+'\n'+reference
        notes=select_rows(memory.notes(ws,item,active_only=True),query)
        note_context=reconstruct_context(conn,notes,query,item_id=item['id'])
        captured={}
        def capture(ws,llm,context,*unused,**kwargs):
            captured['context']=deepcopy(context);raise Captured()
        with patch('lodestar.agent.dialogue.gather',side_effect=capture):
            try:ConversationAgent(ws,None)._turn(row['conversation_id'],row['content'],user_id=user,
                reference_text=reference,reuse_user_message_id=row['id'],history_after=item['context_after'],
                memory_notes=prompt_rows(notes,note_context),track_exposure=False)
            except Captured:pass
        request={}
        class Recorder:
            model=cfg.model
            def dialogue_step(self,system,messages,tools,on_text=None):
                request.update(system=system,messages=deepcopy(messages),tools=tools)
                raise Captured()
        with patch('lodestar.agent.dialogue._execute',side_effect=RuntimeError('Replay cannot execute external tools')):
            try:gather(ws,Recorder(),captured['context'],max_calls=8)
            except Captured:pass
        print(json.dumps({'model':cfg.model,'history_chars':sum(len(m['content']) for m in captured['context']['history']),
            'request_chars':len(request['system'])+sum(len(str(m['content'])) for m in request['messages']),
            'last_message_has_selection':bool(reference) and reference in request['messages'][-1]['content'],
            'history_is_reference_data':all(m['role']=='user' for m in request['messages'])},ensure_ascii=False))
        if args.live:
            client=LLMClient(cfg)
            try:
                reply=client.dialogue_step(request['system'],request['messages'],request['tools'])
                print(json.dumps({'answer':''.join(b['text'] for b in reply['content'] if b['type']=='text'),
                    'tool_requests':sum(b['type']=='tool_use' for b in reply['content']),
                    'observation':client.last_dialogue_observation},ensure_ascii=False))
            finally:client.close()
    finally:conn.close()


if __name__=='__main__':main()
