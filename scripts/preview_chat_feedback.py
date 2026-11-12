"""Offline HTTP-stream fixture for waiting, cancellation, replacement and errors."""
import argparse
import itertools
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import time

from lodestar.config import Config
from lodestar.llm import LLMClient, LLMError
from lodestar.memory import repo
from lodestar.web.server import WebApp, make_server

counter=itertools.count(1)


class Response:
    ok=True
    status_code=200

    def __init__(self, number): self.number=number
    def raise_for_status(self): pass
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def iter_lines(self,**kwargs):
        text=f'这是重新写的第 {self.number} 版回答。\n\n旧回答已经从当前对话移除。这一版会更简洁地说明重点，也会保留必要的来源与判断依据。'
        for index in range(0,len(text),2):
            time.sleep(.035)
            yield 'data: '+json.dumps({'choices':[{'delta':{'content':text[index:index+2]}}]})
        yield 'data: '+json.dumps({'choices':[{'delta':{},'finish_reason':'stop'}]})
        yield 'data: [DONE]'


class Transport:
    def post(self,url,*,json,**kwargs):
        number=next(counter)
        time.sleep(5)  # Deliberately stalled headers, no network or database access.
        message=json['messages'][-1]['content'].rsplit('User request:\n',1)[-1].strip()
        if message=='预览报错': raise LLMError('Synthetic timeout',code='timeout')
        return Response(number)


def client(config):
    llm=LLMClient(config)
    llm.mode='live'
    llm._diandian=SimpleNamespace(base_url='https://offline.preview.invalid',headers={},http=Transport(),
        openai_options=lambda:{},usage=[],close=lambda:None)
    return llm


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8125)
    args=parser.parse_args()
    with TemporaryDirectory(prefix='lodestar-feedback-preview-') as directory:
        root=Path(directory)
        app=WebApp(Config(llm_mode='mock',search_mode='mock',model='offline-feedback',
            db_path=root/'web.db',workspace_dir=root),llm_factory=client)
        with app.workspace() as ws,ws.conn:
            conversation=app.item(ws,'main')['conversation_id']
            repo.add_message(ws.conn,conversation,'user','用一句话解释新的等待交互。')
            repo.add_message(ws.conn,conversation,'assistant','这是一条旧回答，用于验证重新生成后会被替换。')
        server=make_server(app,args.port)
        try:
            print(f'Offline feedback preview: http://127.0.0.1:{server.server_port}',flush=True)
            server.serve_forever()
        finally:
            app.close();server.server_close()


if __name__=='__main__': main()
