"""Disposable slow stream with long Markdown for native scrolling QA."""
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory
import time
from lodestar.config import Config
from lodestar.memory import repo
from lodestar.web.server import WebApp, make_server

class PreviewLLM:
    def __init__(self, config):pass
    def close(self):pass
    def dialogue_step(self, system, messages, tools, on_text=None):
        text='\n\n'+''.join(f'### 新输出 {i}\n把新证据与已有结论对照，说明适用条件和局限。'+
            '这是一段持续生成的离线测试文字，用来验证上滑阅读不会被自动滚动拉回。'*3+'\n\n' for i in range(1,100))
        for i in range(0,len(text),10):
            time.sleep(.075)
            if on_text:on_text(text[i:i+10])
        return {'role':'assistant','content':[{'type':'text','text':text}]}

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--port',type=int,default=8125)
    args=parser.parse_args()
    with TemporaryDirectory(prefix='lodestar-scroll-preview-') as directory:
        root=Path(directory)
        app=WebApp(Config(llm_mode='mock',search_mode='mock',model='offline-scroll',db_path=root/'web.db',workspace_dir=root),llm_factory=PreviewLLM)
        with app.workspace() as ws:
            cid=app.item(ws,'main')['conversation_id']
            for i in range(3):
                repo.add_message(ws.conn,cid,'user',f'历史问题 {i+1}：解释研究中的一个要点。')
                repo.add_message(ws.conn,cid,'assistant',f'## 历史回答 {i+1}\n\n'+('这是一段可向上阅读的历史内容，用于验证生成时的滚动位置。\n\n'*14))
            repo.add_message(ws.conn,cid,'user','继续这篇长回答，我会在生成时上滑阅读。')
            text='## 已经生成的长回答\n\n'+''.join(f'### 阅读位置 {i}\n'+
                '保留完整的上下文与论证，比重复一段总结更有价值。我们需要对照证据、条件和结论，逐步检查每一个判断。'*5+'\n\n' for i in range(1,61))
            repo.add_message(ws.conn,cid,'assistant',text,kind='error',metadata={'partial_answer':text,'model_error':{'code':'truncated'}})
        server=make_server(app,args.port)
        try:
            print(f'Offline scroll preview: http://127.0.0.1:{server.server_port}',flush=True);server.serve_forever()
        finally:app.close();server.server_close()

if __name__=='__main__':main()
