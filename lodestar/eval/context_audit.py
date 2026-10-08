"""Local consistency audit of saved context experiments; never runs model/code."""
import hashlib
import json
from pathlib import Path

from lodestar.eval.context_policy import canonical, validate_protocol, bound_context


def verify(directory):
    root = Path(directory).resolve()
    errors = []
    try:
        protocol_bytes = (root / 'protocol.json').read_bytes()
        protocol = json.loads(protocol_bytes)
        validate_protocol(protocol)
        report = json.loads((root / 'result.json').read_text(encoding='utf-8'))
        if 'environment_hashes' in report:
            from lodestar.eval.environment import verify as verify_environment
            errors.extend(verify_environment(root, report['environment_hashes']))
        if report.get('protocol_sha256') != hashlib.sha256(protocol_bytes).hexdigest():
            errors.append('Protocol hash mismatch or legacy record lacks a hash')
        if report.get('implementation_sha256') != hashlib.sha256((root / 'implementation.py').read_bytes()).hexdigest():
            errors.append('Implementation hash mismatch')
        modes = protocol.get('modes', ['append_only', 'model_edit'])
        if any(mode not in modes for mode in report['arms']):
            errors.append('Unknown arm')
        rates = {}
        stopped = False
        for mode in modes:
            if stopped:
                if mode in report['arms']:
                    errors.append('Execution continued after failed arm')
                continue
            rows = report['arms'][mode]
            processed = 0
            for index, case in enumerate(protocol['cases']):
                if stopped:
                    break
                row = rows[index]
                processed += 1
                folder = root / mode / str(index)
                record = json.loads((folder / 'record.json').read_text(encoding='utf-8'))
                sources = (folder / 'sources.json').read_text(encoding='utf-8')
                if sources != canonical(case['packets']) or record['source_sha256'] != hashlib.sha256(sources.encode('utf-8')).hexdigest():
                    errors.append(mode + ': source ledger differs from protocol')
                state = ''
                output = None
                seen = {}
                accepted = 0
                for turn, event in enumerate(record['events']):
                    packet = case['packets'][turn]
                    expected_input = {'mode': mode, 'context_budget': protocol['context_budget'],
                        'question': case['question'], 'context': state, 'packet': packet}
                    if canonical(event['input']) != canonical(expected_input):
                        errors.append(mode + ': request differs from fixed policy inputs')
                    seen[packet['id']] = packet['text']
                    if 'error' in event:
                        if turn != len(record['events']) - 1 or record['status'] != 'error':
                            errors.append(mode + ': failure status is inconsistent')
                        break
                    response = event['response']
                    citations = response['citations']
                    if not isinstance(response['answer'], str) or not isinstance(citations, list) or any(x not in seen for x in citations):
                        errors.append(mode + ': invalid response citations/answer')
                    if mode == 'append_only':
                        state = (state + '\n' + packet['id'] + ': ' + packet['text'])[-protocol['context_budget']:]
                    elif mode == 'source_bound':
                        state = bound_context(response, seen)
                    else:
                        state = response['context']
                    if not isinstance(state, str) or len(state) > protocol['context_budget']:
                        errors.append(mode + ': context budget exceeded')
                    revision = record['revisions'][accepted]
                    if revision != {'revision': accepted + 1, 'text': state,
                            'sha256': hashlib.sha256(state.encode('utf-8')).hexdigest()}:
                        errors.append(mode + ': revision differs from response')
                    accepted += 1
                    output = {'answer': response['answer'], 'citations': citations}
                if len(record['revisions']) != accepted or record['output'] != output or (folder / 'context.txt').read_text(encoding='utf-8') != state:
                    errors.append(mode + ': final state/output differs from events')
                if record['status'] == 'complete' and len(record['events']) != len(case['packets']):
                    errors.append(mode + ': incomplete turns recorded as complete')
                derived = {'case_id': case['id'], 'status': record['status'], 'output': output,
                    'correct': record['status'] == 'complete' and canonical(output) == canonical(case['expected'])}
                if canonical(row) != canonical(derived):
                    errors.append(mode + ': score differs from independently derived output')
                if record['status'] not in {'complete', 'error'}:
                    errors.append(mode + ': unknown execution status')
                stopped = record['status'] == 'error'
            if len(rows) != processed:
                errors.append(mode + ': unexpected case count')
            rates[mode] = sum(row['correct'] for row in rows) / len(protocol['cases'])
        if report.get('verdict') != ('inconclusive' if stopped else 'measured'):
            errors.append('Verdict differs from completed execution status')
        if not stopped and report.get('rates') != rates:
            errors.append('Rates differ from fixed-case scoring')
        if report.get('adoption_status') != 'not_decided':
            errors.append('Experiment cannot decide adoption')
    except (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError) as exc:
        errors.append(type(exc).__name__ + ': ' + str(exc))
    return {'status': 'invalid' if errors else 'consistent', 'errors': errors,
            'execution': 'not_run', 'semantic_review': 'not_performed',
            'warning': 'Local consistency only; no authenticated execution, semantic entailment or adoption proof.'}
