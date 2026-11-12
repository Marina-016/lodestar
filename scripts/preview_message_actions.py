"""Disposable offline fixture for delete, continue and compact references."""
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory
import time
from lodestar.config import Config
from lodestar.memory import repo
from lodestar.web.server import WebApp, make_server

class PreviewLLM:
    def __init__(self, config): pass
    def close(self): pass
    def dialogue_step(self, system, messages, tools, on_text=None):
        text='好的，接下来我继续。\n\n## 2. 怎样验证改进\n先固定任务与预算，再比较验证器能否识别真实的改进。\n\n- 检查失败样本，避免只优化表面分数。\n- 将训练和评估数据分开。\n\n这就接上了上一部分的论证。'
        for i in range(0,len(text),3):
            time.sleep(.05)
            if on_text:on_text(text[i:i+3])
        return {'role':'assistant','content':[{'type':'text','text':text}]}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8125)
    args=parser.parse_args()
    with TemporaryDirectory(prefix='lodestar-message-actions-') as directory:
        root=Path(directory)
        app=WebApp(Config(llm_mode='mock',search_mode='mock',model='offline-actions',
            db_path=root/'web.db',workspace_dir=root),llm_factory=PreviewLLM)
        with app.workspace() as ws:
            cid=app.item(ws,'main')['conversation_id']
            repo.add_message(ws.conn,cid,'user','这一轮是可删除的测试数据。')
            repo.add_message(ws.conn,cid,'assistant','这一条回答用于验证删除，其他对话会保留。')
            repo.add_message(ws.conn,cid,'user','解释一下研究中 verifier 为什么是瓶颈，接着说说如何验证。')
            partial='## 1. verifier 为什么是瓶颈\n递归自我改进需要一个可靠的判断标准，用来区分真正的提升和表面上的变化。\n\n1. **任务和标准要明确。** 否则很难判断某次修改是否有效。\n2. **验证器也会出错。** 循环可能不断强化它的偏差。\n\n> 引用仍保留自己的样式；错误提示只在正文末尾出现。\n'
            repo.add_message(ws.conn,cid,'assistant',partial+'[回答未完成]',kind='error',metadata={
                'partial_answer':partial,'model_error':{'code':'truncated'},
                'error_message':'已到输出长度上限，回答还没写完。可以点「继续生成」，接着这一条写。'})
        server=make_server(app,args.port)
        try:
            print(f'Disposable offline preview: http://127.0.0.1:{server.server_port}',flush=True)
            server.serve_forever()
        finally:app.close();server.server_close()

if __name__=='__main__':main()
