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
from scripts.chat import add_model_arguments, argument_settings


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

# Qualitative acceptance, not a keyword or length score. Compare under the same
# model/evidence first; evaluate reasoning settings as a separate variable.
QUALITY_QUESTIONS = [
    '不用检索。我的研究Agent有搜索、论文读取、发表状态核验三个工具，总预算8次。'
    '现在经常连续搜索后给出多个小标题，每个标题只有一句概述；开大预算有时仍然如此。'
    '我想每次强制读5篇、增加一个反思Agent、换最贵的模型。请判断这些改动哪个真正值得先做，'
    '解释机制、代价、适用边界，并提出可证伪的小实验。不要只列建议或把长度当作深度。',
    '换一个独立问题，不用检索。以下是虚构实验：A方法在同一基准上成功率由40%升到60%，'
    '平均工具调用由4升到8；B方法在该基准成功率55%，调用数4。每次调用成本相同，'
    '其他成本暂忽略。团队既要控制单次请求费用，也想提高任务完成率。'
    '怎么选择？请深入比较两种目标下的取舍、指标不能说明什么，以及最小验证方案。'
    '这些数字不能被写成真实论文或外部研究结论。',
    '不用检索，用两句话解释刚才为什么不能只看成功率，不要标题。',
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--live-search', action='store_true')
    parser.add_argument('--question', action='append', help='Repeat for a multi-turn scenario')
    parser.add_argument('--greeting', action='store_true', help='Send a greeting before the selected question')
    parser.add_argument('--quality', action='store_true', help='Run analytical-depth cases; requires human review')
    add_model_arguments(parser)
    args = parser.parse_args()
    cfg = load_config()
    if not args.live:
        cfg = replace(cfg, llm_mode='mock')
    try:
        cfg = argument_settings(cfg, args)
    except ValueError as error:
        parser.error(str(error))
    with tempfile.TemporaryDirectory(prefix='lodestar_dialogue_') as temp:
        cfg = replace(cfg, db_path=Path(temp) / 'db', workspace_dir=Path(temp) / 'ws',
                      search_mode='live' if args.live_search else 'mock', demo_replay=False)
        ws = Workspace(cfg)
        try:
            agent = ConversationAgent(ws, ObservedLLM(cfg))
            session = agent.start('dialogue-eval')
            questions = (['你好'] if args.greeting else []) + (args.question or (
                QUALITY_QUESTIONS if args.quality else QUESTIONS))
            for question in questions:
                result = agent.turn(session, question, user_id='dialogue-eval')
                print(json.dumps({'question': question, 'mode': cfg.llm_mode,
                    'status': result['status'], 'answer': result['answer'],
                    'model_error': result.get('model_error'),
                    'search_operations': result.get('grounding', {}).get('search_operations'),
                    'completion_mode': result.get('grounding', {}).get('completion_mode'),
                    'model_configuration': result.get('model_configuration'),
                    'model_calls': result.get('grounding', {}).get('model_calls', []),
                    'tool_calls': len(result.get('dialogue_events', [])),
                    'actions': [{'action': e.get('action'), 'params': e.get('params'), 'cost': e.get('cost', 1),
                                 'error': e.get('result', {}).get('error'),
                                 'query_results': e.get('result', {}).get('query_results'),
                                 'time_window': e.get('result', {}).get('time_window'),
                                 'evidence_assessment': e.get('result', {}).get('evidence_assessment'),
                                 'publication': (e.get('result') if e.get('action') == 'verify_paper'
                                                 else e.get('result', {}).get('publication')),
                                 'results': len(e.get('result', {}).get('sources', []))}
                                for e in result.get('dialogue_events', [])],
                    'contract_valid': result.get('grounding', {}).get('contract_valid')},
                    ensure_ascii=False), flush=True)
                if result['status'] == 'error':
                    raise SystemExit(1)
        finally:
            if 'agent' in locals():
                agent.llm.close()
            ws.close()


if __name__ == '__main__':
    main()
