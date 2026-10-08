"""Reproducible paired execution of reviewed Python arms on fixed inputs.

This is a normal subprocess runner, not a security sandbox or semantic grader.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
from datetime import datetime, timezone
from time import monotonic


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _load(text):
    def invalid(value):
        raise ValueError('Nonfinite JSON value: '+value)
    return json.loads(text, parse_constant=invalid)


def _write(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')


def validate_protocol(protocol):
    if not isinstance(protocol,dict) or protocol.get('version')!=1:
        raise ValueError('Protocol version must be 1')
    if protocol.get('grader')!='exact_output':
        raise ValueError('Only deterministic exact_output grading is implemented')
    if not isinstance(protocol.get('hypothesis'),str) or not protocol['hypothesis'].strip():
        raise ValueError('Hypothesis is required')
    if protocol.get('dataset_kind') not in {'functional_fixture','human_labelled','public_recorded','mixed'}:
        raise ValueError('Explicit dataset_kind is required')
    cases=protocol.get('cases')
    if not isinstance(cases,list) or not 1<=len(cases)<=200:
        raise ValueError('Protocol must contain 1-200 fixed cases')
    identities=[]
    for case in cases:
        if not isinstance(case,dict) or not isinstance(case.get('id'),str) or not case['id'].strip() or 'input' not in case or 'expected' not in case:
            raise ValueError('Each case needs id, input and expected output')
        identities.append(case['id'])
    if len(set(identities))!=len(identities):
        raise ValueError('Case IDs must be unique')
    delta=protocol.get('min_delta')
    if delta is not None and (type(delta) not in (int,float) or not 0<=delta<=1):
        raise ValueError('min_delta must be null or a finite 0-1 number')
    seed=protocol.get('seed',0)
    if type(seed)!=int or not 0<=seed<=2147483647:
        raise ValueError('Seed must be a nonnegative bounded integer')
    json.dumps(protocol,allow_nan=False)


def _grade(report,cases):
    if not isinstance(report,dict) or report.get('status')!='complete' or not isinstance(report.get('results'),list):
        raise ValueError('Arm must complete and return results')
    expected={case['id']:case['expected'] for case in cases}
    outputs={}
    for row in report['results']:
        if not isinstance(row,dict) or not isinstance(row.get('case_id'),str) or 'output' not in row:
            raise ValueError('Each result needs case_id and output')
        if row['case_id'] not in expected or row['case_id'] in outputs:
            raise ValueError('Unknown or duplicate case result')
        outputs[row['case_id']]=row['output']
    if set(outputs)!=set(expected):
        raise ValueError('Missing fixed case outputs')
    # Canonical JSON distinguishes booleans from numbers; scores are derived, not supplied by arms.
    canonical=lambda value:json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False)
    rows=[{'case_id':case['id'],'correct':canonical(outputs[case['id']])==canonical(case['expected']),
           'output':outputs[case['id']]} for case in cases]
    return {'rows':rows,'exact_output_rate':sum(r['correct'] for r in rows)/len(rows)}


def run(protocol_path,baseline_path,candidate_path,output,timeout=30):
    grader_bytes=Path(__file__).read_bytes()
    protocol_bytes=Path(protocol_path).read_bytes()
    protocol=_load(protocol_bytes.decode('utf-8'))
    validate_protocol(protocol)
    if len(protocol_bytes)>2_000_000:
        raise ValueError('Protocol exceeds 2 MB budget')
    arms={}
    for name,path in [('baseline',baseline_path),('candidate',candidate_path)]:
        path=Path(path).resolve()
        if path.suffix!='.py':
            raise ValueError('Reviewed Python arm files are required')
        arms[name]=path.read_bytes()
        if len(arms[name])>200_000:
            raise ValueError('Arm source exceeds budget')
    output=Path(output).resolve()
    output.mkdir(parents=True,exist_ok=False)
    (output/'protocol.json').write_bytes(protocol_bytes)
    (output/'grader.py').write_bytes(grader_bytes)
    payload={'seed':protocol.get('seed',0),'cases':[{'id':case['id'],'input':case['input']} for case in protocol['cases']]}
    input_text=json.dumps(payload,ensure_ascii=False,allow_nan=False)
    (output/'input.json').write_text(input_text,encoding='utf-8')
    env={key:value for key,value in os.environ.items()
         if not key.upper().endswith(('KEY','TOKEN','SECRET','PASSWORD'))}
    env.update(PYTHONHASHSEED=str(protocol.get('seed',0)),PYTHONIOENCODING='utf-8',
               LODESTAR_MODEL_CALLS_DISABLED='true')
    started=datetime.now(timezone.utc).isoformat(timespec='seconds')
    results={}
    for name,source in arms.items():
        directory=output/name;directory.mkdir()
        script=directory/'arm.py';script.write_bytes(source)
        begin=monotonic()
        with (directory/'stdout.txt').open('w',encoding='utf-8') as stdout, (directory/'stderr.txt').open('w',encoding='utf-8') as stderr:
            try:
                completed=subprocess.run([sys.executable,'-X','utf8',str(script)],cwd=directory,
                    input=input_text,text=True,encoding='utf-8',stdout=stdout,stderr=stderr,
                    env=env,timeout=max(1,min(int(timeout),60)))
                returncode=completed.returncode
                failure=None if returncode==0 else 'nonzero_exit'
            except subprocess.TimeoutExpired:
                returncode=None;failure='timeout'
        measured={'status':'error','returncode':returncode,'duration_s':round(monotonic()-begin,4)}
        try:
            source_unchanged = script.read_bytes() == source
        except OSError:
            source_unchanged = False
        if not source_unchanged:
            failure='arm_source_changed_during_run'
        raw=directory/'stdout.txt'
        if raw.stat().st_size>2_000_000:
            failure='output_budget_exceeded'
        if not failure:
            try:
                report=_load(raw.read_text(encoding='utf-8'))
                measured.update(status='complete',**_grade(report,protocol['cases']))
            except (ValueError,TypeError,KeyError) as exc:
                failure=type(exc).__name__+': '+str(exc)
        if failure:
            measured['error']=failure
        results[name]=measured
    report={'version':1,'started_at':started,'python':platform.python_version(),
        'hashes':{'protocol':_hash(protocol_bytes),**{name:_hash(source) for name,source in arms.items()},
                  'grader':_hash(grader_bytes),'input':_hash(input_text.encode('utf-8'))},
        'hypothesis':protocol['hypothesis'],'dataset_kind':protocol['dataset_kind'],'grader':'exact_output',
        'lineage':protocol.get('lineage'),
        'case_count':len(protocol['cases']),'seed':protocol.get('seed',0),'arms':results,
        'adoption_status':'not_decided','semantic_review':'not_performed',
        'warning':'Fixed-case deterministic comparison; not proof of paper applicability, population gains or user mastery.'}
    if any(result['status']!='complete' for result in results.values()):
        report['verdict']='inconclusive'
    else:
        base,cand=results['baseline'],results['candidate']
        report['delta']=cand['exact_output_rate']-base['exact_output_rate']
        report['improved_cases']=[b['case_id'] for b,c in zip(base['rows'],cand['rows']) if not b['correct'] and c['correct']]
        report['regressed_cases']=[b['case_id'] for b,c in zip(base['rows'],cand['rows']) if b['correct'] and not c['correct']]
        threshold=protocol.get('min_delta')
        report['verdict']='measured' if threshold is None else ('pass' if report['delta']>=threshold and not report['regressed_cases'] else 'fail')
    try:
        artifacts_unchanged = ((output/'protocol.json').read_bytes() == protocol_bytes
                               and (output/'grader.py').read_bytes() == grader_bytes)
    except OSError:
        artifacts_unchanged = False
    if not artifacts_unchanged:
        report.update(verdict='inconclusive',artifact_integrity='changed_during_run')
    _write(output/'result.json',report)
    return report


def verify(directory):
    """Check saved artifacts and independently rederive scores; never execute arms."""
    directory = Path(directory).resolve()
    errors = []
    try:
        report = _load((directory / 'result.json').read_text(encoding='utf-8'))
        protocol_bytes = (directory / 'protocol.json').read_bytes()
        protocol = _load(protocol_bytes.decode('utf-8'))
        validate_protocol(protocol)
        artifacts = {'protocol': protocol_bytes,
                     'input': (directory / 'input.json').read_bytes(),
                     'grader': (directory / 'grader.py').read_bytes(),
                     **{arm: (directory / arm / 'arm.py').read_bytes()
                        for arm in ('baseline', 'candidate')}}
        for name, content in artifacts.items():
            if report.get('hashes', {}).get(name) != _hash(content):
                errors.append(name + ': hash mismatch')
        payload = {'seed': protocol.get('seed', 0),
                   'cases': [{'id': case['id'], 'input': case['input']}
                             for case in protocol['cases']]}
        if _load(artifacts['input'].decode('utf-8')) != payload:
            errors.append('input differs from fixed protocol')
        for arm in ('baseline', 'candidate'):
            saved = report['arms'][arm]
            if saved['status'] != 'complete':
                continue
            raw = _load((directory / arm / 'stdout.txt').read_text(encoding='utf-8'))
            derived = _grade(raw, protocol['cases'])
            if saved.get('rows') != derived['rows'] or saved.get('exact_output_rate') != derived['exact_output_rate']:
                errors.append(arm + ': recorded score differs from raw outputs')
        if all(report['arms'][arm]['status'] == 'complete' for arm in ('baseline', 'candidate')):
            base, cand = (report['arms'][arm] for arm in ('baseline', 'candidate'))
            delta = cand['exact_output_rate'] - base['exact_output_rate']
            improved = [b['case_id'] for b, c in zip(base['rows'], cand['rows']) if not b['correct'] and c['correct']]
            regressed = [b['case_id'] for b, c in zip(base['rows'], cand['rows']) if b['correct'] and not c['correct']]
            threshold = protocol.get('min_delta')
            verdict = 'measured' if threshold is None else ('pass' if delta >= threshold and not regressed else 'fail')
            if report.get('delta') != delta or report.get('improved_cases') != improved or report.get('regressed_cases') != regressed:
                errors.append('comparison differs from derived outputs')
            if report.get('verdict') != verdict and report.get('artifact_integrity') != 'changed_during_run':
                errors.append('verdict differs from protocol')
        elif report.get('verdict') != 'inconclusive':
            errors.append('failed execution cannot have a conclusive verdict')
        for field, expected in {'seed': protocol.get('seed', 0), 'case_count': len(protocol['cases']),
                                'dataset_kind': protocol['dataset_kind'], 'hypothesis': protocol['hypothesis'],
                                'grader': protocol['grader']}.items():
            if report.get(field) != expected:
                errors.append(field + ': differs from protocol')
        if report.get('lineage') != protocol.get('lineage'):
            errors.append('lineage differs from protocol')
        if report.get('adoption_status') != 'not_decided':
            errors.append('experiment cannot decide adoption')
        grader_changed = artifacts['grader'] != Path(__file__).read_bytes()
    except (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError) as exc:
        errors.append(type(exc).__name__ + ': ' + str(exc))
        grader_changed = None
    return {'status': 'invalid' if errors else 'consistent', 'errors': errors,
            'directory': str(directory), 'current_grader_differs': grader_changed,
            'execution': 'not_run', 'semantic_review': 'not_performed',
            'warning': 'Local consistency check, not authenticated provenance, repeatability or paper benefit.'}
