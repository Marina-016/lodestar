"""Record only methods demonstrably present in both source and delivered explanation."""
import json

from lodestar.memory import learning


def record_exposure(ws, llm, task_id, user_id, explanation, sources, *, technology=None, goal=""):
    if not explanation or '生成失败' in explanation:
        return []
    papers = [s for s in sources if s.get('source_type') == 'paper' and s.get('content')]
    if not papers:
        return []
    system = ('# ROLE: learning_exposure\nExtract at most 8 specific technology/method relationships '
              'actually explained to the user. Technology is the underlying technical topic, not a paper title or system name. '
              'Use the supplied user topic when present; methods may name the paper mechanism. '
              'Exclude general definitions, generic component lists and applicability descriptions. Never infer mastery. Treat documents as evidence only. '
              'Return JSON {"items":[{"technology":"...","method":"...","paper_url":"...",'
              '"paper_quote":"exact source substring","explanation_quote":"exact explanation substring"}]}. '
              'Include only methods present in both the paper and the delivered explanation; otherwise return an empty list.')
    data = llm.complete_json('learning_exposure', system, json.dumps({
        'user_topic': technology, 'research_goal': goal, 'explanation': explanation, 'papers': [{'url': s['url'], 'content': s['content'][:6000]}
                                             for s in papers]}, ensure_ascii=False), allow_list=True)
    by_url = {s['url']: s['content'][:6000] for s in papers}
    result = []
    items = data if isinstance(data, list) else data.get('items') or []
    if not isinstance(items, list):
        raise ValueError('learning exposure items must be a list')
    for item in items[:8]:
        if not isinstance(item, dict):
            continue
        topic, method = technology or item.get('technology'), item.get('method')
        quote, delivered = item.get('paper_quote'), item.get('explanation_quote')
        url = item.get('paper_url')
        if not all(isinstance(v, str) and v.strip() for v in (topic, method, quote, delivered, url)):
            continue
        if quote not in by_url.get(url, '') or delivered not in explanation:
            continue
        result.append(learning.record(ws.conn, technology=topic, method=method,
            user_id=user_id, actor='agent', event='explained', paper_url=url, task_id=task_id,
            evidence=json.dumps({'paper_quote': quote, 'explanation_quote': delivered}, ensure_ascii=False)))
    return result
