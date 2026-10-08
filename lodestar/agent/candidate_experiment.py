"""Attach explicit reviewed A/B executions to immutable candidate-plan lineage."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile

from lodestar.agent.candidate import _recommendation
from lodestar.eval.paired import run


def execute(ws, project_id, recommendation_id, plan_id, protocol, baseline, candidate, output, timeout=30):
    _recommendation(ws, project_id, recommendation_id)
    row=ws.conn.execute('SELECT * FROM paper_candidate_plans WHERE id=? AND recommendation_id=?',
                        (plan_id,recommendation_id)).fetchone()
    if row is None:
        raise ValueError('Plan does not belong to this candidate')
    plan=json.loads(row['result'])
    if row['status']!='draft' or not plan.get('contract_valid'):
        raise ValueError('A contract-valid draft is required; this does not approve its semantics')
    if plan.get('action', 'propose_change') != 'propose_change':
        raise ValueError('Investigation/no-change drafts do not authorize a candidate implementation experiment')
    spec=json.loads(Path(protocol).read_text(encoding='utf-8'))
    if not isinstance(spec,dict):
        raise ValueError('Protocol must be an object')
    spec['lineage']={'project_id':project_id,'recommendation_id':recommendation_id,'plan_id':plan_id,
        'assessment_id':row['assessment_id'],'read_id':row['read_id'],
        'plan_sha256':hashlib.sha256(row['result'].encode('utf-8')).hexdigest(),
        'plan_mode':plan.get('mode','unknown'),'applicability_review':plan.get('applicability_review','required')}
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',suffix='.json',
                                          dir=ws.config.workspace_dir,delete=False) as stream:
            temporary=Path(stream.name)
            json.dump(spec,stream,ensure_ascii=False,allow_nan=False)
        result=run(temporary,baseline,candidate,output,timeout)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    with ws.conn:
        cursor=ws.conn.execute('INSERT INTO paper_candidate_experiments(recommendation_id,plan_id,result_path,result,created_at) VALUES(?,?,?,?,?)',
            (recommendation_id,plan_id,str(Path(output).resolve()/'result.json'),json.dumps(result,ensure_ascii=False),
             datetime.now(timezone.utc).isoformat(timespec='seconds')))
    return {**result,'experiment_id':cursor.lastrowid,'lineage':spec['lineage']}


def history(ws,project_id,recommendation_id):
    _recommendation(ws,project_id,recommendation_id)
    rows=ws.conn.execute('SELECT * FROM paper_candidate_experiments WHERE recommendation_id=? ORDER BY id DESC LIMIT 20',
                         (recommendation_id,)).fetchall()
    return [{**dict(row),'result':json.loads(row['result'])} for row in rows]


def record_investigation(ws, project_id, recommendation_id, plan_id, directory):
    """Register an existing audited diagnostic; does not execute or approve it."""
    _recommendation(ws, project_id, recommendation_id)
    row = ws.conn.execute('SELECT * FROM paper_candidate_plans WHERE id=? AND recommendation_id=?',
                         (plan_id, recommendation_id)).fetchone()
    if row is None:
        raise ValueError('Plan does not belong to this candidate')
    plan = json.loads(row['result'])
    if row['status'] != 'draft' or not plan.get('contract_valid') or plan.get('action') != 'investigate':
        raise ValueError('An investigation draft is required, not an implementation/no-change draft')
    from lodestar.eval.context_audit import verify
    directory = Path(directory).resolve()
    protocol = json.loads((directory / 'protocol.json').read_text(encoding='utf-8'))
    if protocol.get('paper') not in plan.get('paper_urls', []):
        raise ValueError('Diagnostic paper must match the investigation draft evidence')
    check = verify(directory)
    if check['status'] != 'consistent':
        raise ValueError('Investigation record failed consistency audit: ' + '; '.join(check['errors']))
    raw = (directory / 'result.json').read_bytes()
    result = {'report': json.loads(raw), 'audit': check,
        'report_sha256': hashlib.sha256(raw).hexdigest(),
        'lineage': {'project_id': project_id, 'recommendation_id': recommendation_id,
            'plan_id': plan_id, 'assessment_id': row['assessment_id'], 'read_id': row['read_id'],
            'plan_sha256': hashlib.sha256(row['result'].encode('utf-8')).hexdigest()},
        'registration': 'after_execution', 'execution_provenance': 'imported_local_record',
        'adoption_status': 'not_decided', 'mastery_effect': 'none', 'model_calls': 0}
    with ws.conn:
        cursor = ws.conn.execute('INSERT INTO paper_candidate_investigations(recommendation_id,plan_id,result_path,result,created_at) VALUES(?,?,?,?,?)',
            (recommendation_id, plan_id, str(directory/'result.json'), json.dumps(result, ensure_ascii=False),
             datetime.now(timezone.utc).isoformat(timespec='seconds')))
    return {**result, 'investigation_id': cursor.lastrowid}


def investigations(ws, project_id, recommendation_id):
    _recommendation(ws, project_id, recommendation_id)
    rows = ws.conn.execute('SELECT * FROM paper_candidate_investigations WHERE recommendation_id=? ORDER BY id DESC LIMIT 20',
                           (recommendation_id,)).fetchall()
    return [{**dict(row), 'result': json.loads(row['result'])} for row in rows]
