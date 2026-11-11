"""Isolated diagram UI fixture; --live makes one synthetic model request."""
import argparse
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from lodestar.config import Config, load_config
from lodestar.agent.conversation import ConversationAgent
from lodestar.llm import LLMClient
from lodestar.memory import repo
from lodestar.web.server import WebApp, make_server

SAMPLE = '''下面用三种示意图解释 Agent 记忆。它们是整理后的示意图，不是论文原图。

### 流程与条件判断
```mermaid
flowchart LR
  A["收到问题"] --> B["检索记忆线索"]
  B --> C{"证据够用吗？"}
  C -->|是| D["生成有依据的回答"]
  C -->|否| E["回溯原始对话"]
  E --> D
```

### 记忆层级
```mermaid
mindmap
  root((Agent 记忆))
    短期记忆
      当前任务
      最近对话
    长期记忆
      用户偏好
      项目决策
    参考资料
      论文链接
      文档索引
```

### 交互时序
```mermaid
sequenceDiagram
  participant U as 用户
  participant A as 助手
  participant M as 记忆库
  U->>A: 提出问题
  A->>M: 查找相关线索
  M-->>A: 返回来源与片段
  A-->>U: 回答并给出引用
```

### 格式错误时保留原代码
```mermaid
flowchart LR
  A[broken
```
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8125)
    parser.add_argument('--live', action='store_true', help='One synthetic model-generated diagram response')
    args = parser.parse_args()
    with TemporaryDirectory(prefix='lodestar-diagram-preview-') as folder:
        root = Path(folder)
        config = (replace(load_config(), db_path=root/'web.db', workspace_dir=root) if args.live else
                  Config(llm_mode='mock', search_mode='mock', model='offline-preview',
                         db_path=root/'web.db', workspace_dir=root))
        app = WebApp(config)
        with app.workspace() as ws, ws.conn:
            item = app.item(ws, 'main')
            if args.live:
                client = LLMClient(config)
                try:
                    result = ConversationAgent(ws, client).turn(item['conversation_id'],
                        '不要联网。用简洁的中文说明接收问题、检索记忆、证据不足时回溯原文、生成回答的流程，'
                        '并画一张真正可渲染的流程图，最多六个节点。',
                        user_id=app.user(), track_exposure=False, execution_policy={'external_retrieval':'blocked'})
                    print('Live response status: ' + result['status'], flush=True)
                    print('Mermaid block generated: ' + str('```mermaid' in result['answer']), flush=True)
                finally:
                    client.close()
            else:
                repo.add_message(ws.conn,item['conversation_id'],'user','用流程图、层级图和时序图解释 Agent 记忆。')
                repo.add_message(ws.conn,item['conversation_id'],'assistant',SAMPLE)
        server = make_server(app,args.port)
        try:
            print(f'Diagram preview: http://127.0.0.1:{server.server_port}',flush=True)
            server.serve_forever()
        finally:
            app.close(); server.server_close()


if __name__ == '__main__':
    main()
