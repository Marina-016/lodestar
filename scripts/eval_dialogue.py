"""Small live dialogue acceptance set; uses an isolated DB and no project files.

Run from repository root: python -m scripts.eval_dialogue --live
Fixtures test plumbing only; real output requires explicit --live and configured LLM.
"""
import argparse
import json
import tempfile
from dataclasses import replace
from pathlib import Path

from lodestar.agent.conversation import ConversationAgent
from lodestar.config import load_config
from lodestar.context import Workspace
from lodestar.llm import LLMClient, LLMError


class ObservedLLM(LLMClient):
    def complete_json(self, *args, **kwargs):
        try:
            return super().complete_json(*args, **kwargs)
        except LLMError as error:
            print(json.dumps({'error_type': type(error).__name__,
                'cause_type': type(error.__cause__).__name__,
                'local_type_error': str(error.__cause__) if isinstance(error.__cause__, TypeError) else None,
                'http_status': getattr(error.__cause__, 'status_code', None)}), flush=True)
            raise


QUESTIONS = [
    '不用检索，用一句话解释 Agent harness，面向刚学编程的人。',
    '把刚才的解释换成餐厅的类比，不要分标题。',
    '换个话题：用一个小表格比较 Python 的 list 和 tuple，不用搜索。',
    '给一个不超过 8 行的 Python 缓存例子，代码后只解释一句，不用检索。',
    'Translate into English: 先确认问题，再按需调用工具。不要搜索。',
    '我的项目还没有代码，你建议先验证什么？只给两个可执行的小步骤，不用检索。',
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--live-search', action='store_true')
    parser.add_argument('--question')
    args = parser.parse_args()
    cfg = load_config()
    if not args.live:
        cfg = replace(cfg, llm_mode='mock')
    with tempfile.TemporaryDirectory(prefix='lodestar_dialogue_') as temp:
        cfg = replace(cfg, db_path=Path(temp) / 'db', workspace_dir=Path(temp) / 'ws',
                      search_mode='live' if args.live_search else 'mock', demo_replay=False,
                      max_tokens=2500, llm_timeout_s=45)
        ws = Workspace(cfg)
        try:
            agent = ConversationAgent(ws, ObservedLLM(cfg))
            session = agent.start('dialogue-eval')
            for question in ([args.question] if args.question else QUESTIONS):
                result = agent.turn(session, question, user_id='dialogue-eval')
                print(json.dumps({'question': question, 'mode': cfg.llm_mode,
                    'status': result['status'], 'answer': result['answer'],
                    'tool_calls': len(result.get('dialogue_events', [])),
                    'actions': [{'action': e.get('action'), 'params': e.get('params'),
                                 'error': e.get('result', {}).get('error'),
                                 'results': len(e.get('result', {}).get('sources', []))}
                                for e in result.get('dialogue_events', [])],
                    'contract_valid': result.get('grounding', {}).get('contract_valid')},
                    ensure_ascii=False), flush=True)
                if result['status'] == 'error':
                    raise SystemExit(1)
        finally:
            ws.close()


if __name__ == '__main__':
    main()
