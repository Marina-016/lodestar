"""Validation of a technical proposal against the exact evidence sent to the model."""
from __future__ import annotations


def canonicalize_quotes(proposal, documents, papers):
    """Restore PDF line wrapping only, retaining exact source spans for audit.

    No word substitution, punctuation normalization or semantic matching.
    """
    import re
    if not isinstance(proposal, dict):
        return
    for section, field, key, sources in (
            ('problem', 'project_refs', 'path', documents),
            ('method', 'paper_refs', 'url', papers)):
        block = proposal.get(section)
        if not isinstance(block, dict) or not isinstance(block.get(field), list):
            continue
        allowed = {source[key]: source['content'] for source in sources}
        for reference in block[field]:
            if not isinstance(reference, dict):
                continue
            locator, quote = reference.get(key), reference.get('quote')
            if not isinstance(locator, str) or locator not in allowed or not isinstance(quote, str) or len(quote.strip()) < 20:
                continue
            content = allowed[locator]
            pattern = r'\s+'.join(re.escape(word) for word in re.split(r'\s+', quote.strip()))
            match = re.search(pattern, content)
            if match:
                reference['quote_original'] = quote
                reference['quote'] = match.group(0)
                reference['excerpt_span'] = {'start': match.start(), 'end': match.end()}
                reference['whitespace_restored'] = quote != match.group(0)


def validate(proposal, documents, papers):
    errors = []
    if not isinstance(proposal, dict):
        return ['proposal must be an object']
    for field in ('problem', 'method', 'experiment'):
        if not isinstance(proposal.get(field), dict):
            errors.append(field + ' must be an object')
    for field in ('changes', 'risks', 'missing_evidence'):
        if not isinstance(proposal.get(field), list):
            errors.append(field + ' must be a list')
    if errors:
        return errors
    doc_by_path = {d['path']: d['content'] for d in documents}
    paper_by_url = {p['url']: p['content'] for p in papers}
    def refs(section, field, allowed, key):
        items = section.get(field)
        if not isinstance(items, list) or not items:
            errors.append(field + ' requires at least one evidence reference')
            return
        for item in items:
            if not isinstance(item, dict):
                errors.append(field + ' reference must be an object'); continue
            locator, quote = item.get(key), item.get('quote')
            if locator not in allowed:
                errors.append(field + ' references unknown evidence'); continue
            if not isinstance(quote, str) or len(quote.strip()) < 20 or quote not in allowed[locator]:
                errors.append(field + ' quote must be an exact nontrivial supplied substring')
    for name in ('problem', 'method'):
        if not isinstance(proposal[name].get('description'), str) or not proposal[name]['description'].strip():
            errors.append(name + ' description is required')
    refs(proposal['problem'], 'project_refs', doc_by_path, 'path')
    refs(proposal['method'], 'paper_refs', paper_by_url, 'url')
    changes = proposal['changes']
    action = proposal.get('action', 'propose_change')
    if action not in {'propose_change', 'investigate', 'no_change'}:
        errors.append('Unknown proposal action')
    elif action == 'propose_change':
        if not 1 <= len(changes) <= 3:
            errors.append('changes must contain 1-3 bounded changes')
    else:
        if changes:
            errors.append('investigate/no_change must not include implementation changes')
        if not isinstance(proposal.get('action_reason'), str) or not proposal['action_reason'].strip():
            errors.append('investigate/no_change requires an explicit reason')
    for change in changes:
        if not isinstance(change, dict) or change.get('path') not in doc_by_path or not isinstance(change.get('description'), str) or not change.get('description', '').strip():
            errors.append('change must name a supplied path and describe an intervention')
    experiment = proposal['experiment']
    for field in ('hypothesis', 'baseline', 'candidate'):
        if not isinstance(experiment.get(field), str) or not experiment[field].strip():
            errors.append('experiment ' + field + ' is required')
    if not isinstance(experiment.get('metrics'), list) or not experiment.get('metrics') or not all(isinstance(x,str) and x.strip() for x in experiment['metrics']):
        errors.append('experiment metrics must be nonempty strings')
    if not isinstance(experiment.get('constraints'), list) or not all(isinstance(x,str) for x in experiment['constraints']):
        errors.append('experiment constraints must be strings')
    for name in ('risks','missing_evidence'):
        if not all(isinstance(x,str) for x in proposal[name]):
            errors.append(name + ' must contain only strings')
    return errors


def render(proposal):
    lines = ['## 技术方案草案', '语义适用性待审查；未执行实验，不代表已采用。',
             '\n### 项目问题', proposal['problem']['description']]
    for ref in proposal['problem']['project_refs']:
        lines.append('- 项目依据：`' + ref['path'] + '`')
    lines.extend(['\n### 论文方法与项目假设', proposal['method']['description']])
    for ref in proposal['method']['paper_refs']:
        lines.append('- 论文依据：' + ref['url'])
    action = proposal.get('action', 'propose_change')
    lines.append('\n### 建议动作：' + {'propose_change': '提出改动', 'investigate': '先调查', 'no_change': '无需改动'}[action])
    if action != 'propose_change':
        lines.append(proposal['action_reason'])
    lines.append('\n### 候选改动')
    lines.extend('- `' + c['path'] + '`：' + c['description'] for c in proposal['changes'])
    e=proposal['experiment']
    lines.extend(['\n### 待执行对比协议', '假设：'+e['hypothesis'], 'Baseline：'+e['baseline'],
                  'Candidate：'+e['candidate'], '指标：'+'、'.join(e['metrics']),
                  '约束：'+'；'.join(e['constraints']), '\n### 风险'])
    lines.extend('- '+x for x in proposal['risks'])
    lines.append('\n### 未验证项')
    lines.extend('- '+x for x in proposal['missing_evidence'])
    return '\n'.join(lines)
