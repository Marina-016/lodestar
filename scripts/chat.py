"""Interactive terminal for the current ConversationAgent backend."""
from dataclasses import replace
from pathlib import Path

from lodestar.agent.conversation import ConversationAgent
from lodestar.config import load_config
from lodestar.context import Workspace
from lodestar.llm import LLMClient


def main():
    cfg = load_config()
    root = Path(__file__).resolve().parents[1] / 'workspace' / 'interactive-test'
    cfg = replace(cfg, db_path=root / 'chat.db', workspace_dir=root,
                  demo_replay=False)
    ws = Workspace(cfg)
    try:
        agent = ConversationAgent(ws, LLMClient(cfg))
        session = agent.start()
        print(f'Lodestar 本地对话测试 | {cfg.llm_mode} | {cfg.model}', flush=True)
        print('输入问题后回车。/new 新对话，/exit 退出。\n'
              '此入口直接调用新后端；测试记录保存在 workspace/interactive-test。', flush=True)
        while True:
            try:
                message = input('\n你 > ').strip()
            except (EOFError, KeyboardInterrupt):
                break
            if message == '/exit':
                break
            if message == '/new':
                session = agent.start()
                print('已开始新对话。', flush=True)
                continue
            if not message:
                continue
            print('思考中…', flush=True)
            result = agent.turn(session, message)
            print('\nLodestar > ' + result.get('answer', result.get('error', '未返回答案')), flush=True)
    finally:
        ws.close()


if __name__ == '__main__':
    main()
