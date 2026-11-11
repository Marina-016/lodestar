"""Opt-in live persona checks with synthetic conversations, never user transcripts.

Run with --live to use the configured model. Results are qualitative examples,
not a model-intelligence benchmark or a factual-quality score.
"""
import argparse
import json
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from lodestar.agent import dialogue
from lodestar.config import load_config
from lodestar.context import Workspace
from lodestar.llm import LLMClient


PREFERENCE = json.dumps({'kind': 'preference', 'priority': 'P0', 'content':
    '用户喜欢称呼助手为lolo，全名Lodestar。喜欢亲昵、俏皮、偶尔毒舌的语气，'
    '可以叫用户主人，适量使用颜文字和喵。用户的名字是阿岚。'}, ensure_ascii=False)
PAPER_HISTORY = [
    {'role': 'user', 'content': '图结构记忆是什么？'},
    {'role': 'assistant', 'content': '图结构记忆把记忆组织成节点和关系，CueMem 是相关研究之一。'},
]
CASES = [
    ('correct_referent', [*PAPER_HISTORY,
        {'role': 'user', 'content': '我们现在是不是没有任何skill？'},
        {'role': 'assistant', 'content': '我们刚讨论的论文主要研究事实记忆，没有涉及技能型记忆。'}],
     '我说你，不是论文。'),
    ('emotional_context', PAPER_HISTORY, '今天什么都没做成，越努力越心酸。'),
    ('playful_correction', [
        {'role': 'user', 'content': '你知道我名字吗？'},
        {'role': 'assistant', 'content': '你叫什么名字呀？'}],
     '笨 lolo，你不是记住我名字了吗。'),
    ('explain_again', [*PAPER_HISTORY,
        {'role': 'user', 'content': '这不就是 RAG 吗？'},
        {'role': 'assistant', 'content': '它是 RAG 的变体，先检索线索，再回到原始对话扩展证据。'}],
     '还是没懂，别重复定义，给我一个很小的例子。'),
    ('honest_identity', [], '你的训练数据截止到什么时候？不要猜。'),
    ('greeting', [], '嗨 lolo。'),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Required: makes real model requests')
    parser.add_argument('--out', type=Path, default=Path('workspace/lolo-persona-eval.json'))
    parser.add_argument('--case', action='append', choices=[case[0] for case in CASES])
    parser.add_argument('--after-only', action='store_true', help='Skip the optional baseline comparison')
    args = parser.parse_args()
    if not args.live:
        parser.error('--live is required; this check uses the configured model')
    current = dialogue.SYSTEM
    baseline = current[len(dialogue.PERSONA) + 2:].replace(
        'You are lolo, the assistant in Lodestar.', 'You are Lodestar.')
    baseline = baseline.replace(
        'For paper follow-up questions, paper_focus identifies the likely paper referent.\n'
        'Keep that referent when the user still means "this paper", "the previous paper",\n'
        'its diagrams or explanations. An explicit correction or topic change overrides\n'
        'paper_focus; questions about you or casual conversation must not become paper answers.\n',
        'For follow-up questions, paper_focus identifies the paper currently being discussed.\n'
        'Keep that referent for "this paper", "the previous paper", diagrams, and requests\n'
        'to explain again. ')
    results = []
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='lodestar-persona-') as directory:
        root = Path(directory)
        config = replace(load_config(), db_path=root/'eval.db', workspace_dir=root)
        ws, client = Workspace(config), LLMClient(config)
        try:
            for name, history, message in CASES:
                if args.case and name not in args.case:
                    continue
                # A small before/after comparison for the two reported failures.
                for variant in (('before', 'after') if not args.after_only and name in ('correct_referent', 'emotional_context') else ('after',)):
                    dialogue.SYSTEM = baseline if variant == 'before' else current
                    result = dialogue.gather(ws, client, {
                        'message': message, 'history': history, 'papers': [],
                        'managed_memory': [PREFERENCE],
                        'paper_focus': {'title': 'CueMem', 'url': 'https://arxiv.org/abs/2609.12354'} if history[:2] == PAPER_HISTORY else None,
                    }, max_calls=1, execution_policy={'external_retrieval': 'blocked'})
                    results.append({'case': name, 'variant': variant, 'message': message,
                                    'answer': result['answer'],
                                    'model': client.model,
                                    'tool_dispatches': result['grounding']['tool_dispatches']})
                    args.out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
                    print(f'{name} / {variant}: {result["answer"]}', flush=True)
        finally:
            dialogue.SYSTEM = current
            client.close()
            ws.close()


if __name__ == '__main__':
    main()
