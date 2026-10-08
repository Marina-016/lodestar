"""Read public paper candidates and hand bounded evidence to a project session."""
from datetime import datetime, timezone
import json

from lodestar.memory import repo
from lodestar.tools.paper_read import tool_read_paper


def _recommendation(ws, project_id, recommendation_id):
    row = ws.conn.execute('SELECT * FROM paper_recommendations WHERE id=? AND project_id=?',
                          (recommendation_id, project_id)).fetchone()
    if row is None:
        raise ValueError('Recommendation does not belong to this project')
    return {**dict(row), 'source': json.loads(row['source'])}


def evidence(ws, project_id, recommendation_id):
    _recommendation(ws, project_id, recommendation_id)
    row = ws.conn.execute("""SELECT * FROM paper_candidate_reads WHERE recommendation_id=?
        AND status='read' ORDER BY CASE WHEN json_extract(evidence,'$.read_depth')='full' THEN 0 ELSE 1 END,id DESC LIMIT 1""", (recommendation_id,)).fetchone()
    if row is None:
        return None
    return {**dict(row), 'evidence':json.loads(row['evidence'])}


def read(ws, project_id, recommendation_id, *, refresh=False, reader=tool_read_paper):
    recommendation = _recommendation(ws, project_id, recommendation_id)
    fixture = recommendation['paper_key'].startswith('fixture:')
    if fixture != (ws.config.search_mode == 'mock'):
        raise ValueError('Candidate provenance and reader mode differ; use the corresponding workspace mode')
    previous = evidence(ws, project_id, recommendation_id)
    if previous and not refresh:
        return {'status':'read','cached':True,'read_id':previous['id'],'evidence':previous['evidence']}
    source = recommendation['source']
    budget = max(500,min(ws.config.read_char_budget,24000))
    try:
        result = reader(ws, source['url'], char_budget=budget, full_text=True, query=source.get('title',''))
    except Exception as exc:
        result = {'error':type(exc).__name__}
    status = 'error' if result.get('error') or not result.get('text','').strip() else 'read'
    body = result.get('text','')[:budget]
    snapshot = {**source,'source_type':'paper','content':body,
                'read_depth':result.get('read_depth','unread'),
                'coverage':result.get('coverage','unspecified'),
                'evidence_spans':result.get('evidence_spans',[]),
                'note':result.get('note',''), 'read_error':result.get('error'),
                'read_mode':'mock' if fixture else 'live',
                'truncated':bool(result.get('truncated') or len(result.get('text',''))>budget),
                'semantic_applicability':'pending', 'mastery_effect':'none'}
    stamp=datetime.now(timezone.utc).isoformat(timespec='seconds')
    with ws.conn:
        cursor=ws.conn.execute('INSERT INTO paper_candidate_reads(recommendation_id,status,evidence,created_at) VALUES(?,?,?,?)',
                               (recommendation_id,status,json.dumps(snapshot,ensure_ascii=False),stamp))
        if status=='read':
            ws.conn.execute("UPDATE paper_recommendations SET state='read' WHERE id=?",(recommendation_id,))
    return {'status':status,'cached':False,'read_id':cursor.lastrowid,'evidence':snapshot,
            'previous_evidence_retained':bool(previous),'model_calls':0}


def handoff(ws, project_id, recommendation_id, user_id='default'):
    saved = evidence(ws, project_id, recommendation_id)
    if saved is None:
        raise ValueError('Read the candidate successfully before starting a session')
    from lodestar.agent.conversation import ConversationAgent
    agent=ConversationAgent(ws,None)
    session=agent.start(user_id,project_id)
    with ws.conn:
        ws.conn.execute('UPDATE agent_sessions SET evidence=? WHERE conversation_id=?',
                        (json.dumps([saved['evidence']],ensure_ascii=False),session))
        repo.add_message(ws.conn,session,'assistant',
            '已接入候选论文的有界阅读证据；项目适用性仍待评估，未执行实验。',
            kind='candidate_evidence',metadata={'recommendation_id':recommendation_id,'read_id':saved['id']})
    return {'conversation_id':session,'project_id':project_id,'recommendation_id':recommendation_id,
            'read_id':saved['id'],'semantic_applicability':'pending','model_calls':0}


def assess(ws,llm,project_id,recommendation_id,goal):
    saved=evidence(ws,project_id,recommendation_id)
    if saved is None:
        raise ValueError('Read the candidate before assessing it')
    from lodestar.agent.applicability import evaluate
    result=evaluate(ws,llm,goal,[saved['evidence']],project_id)
    with ws.conn:
        cursor=ws.conn.execute('INSERT INTO paper_candidate_assessments(recommendation_id,read_id,status,result,created_at) VALUES(?,?,?,?,?)',
            (recommendation_id,saved['id'],result['status'],json.dumps(result,ensure_ascii=False),
             datetime.now(timezone.utc).isoformat(timespec='seconds')))
    return {**result,'assessment_id':cursor.lastrowid,'read_id':saved['id']}


def plan(ws,llm,project_id,recommendation_id,goal,assessment_id=None):
    saved=evidence(ws,project_id,recommendation_id)
    if saved is None:
        raise ValueError('Read the candidate before generating a plan')
    query='SELECT * FROM paper_candidate_assessments WHERE recommendation_id=?'
    parameters=[recommendation_id]
    if assessment_id is not None:
        query+=' AND id=?';parameters.append(assessment_id)
    else:
        query+=" AND status='assessed'"
    row=ws.conn.execute(query+' ORDER BY id DESC LIMIT 1',parameters).fetchone()
    if row is None:
        return {'status':'needs_assessment','model_calls':0}
    assessment=json.loads(row['result'])
    result={'status':'needs_assessment','model_calls':0}
    if row['read_id']!=saved['id']:
        result['status']='needs_reassessment'
    elif row['status']!='assessed' or not assessment.get('contract_valid'):
        result['reason']='Assessment has not passed the evidence contract'
    elif assessment['assessment']['decision']=='not_applicable':
        result['status']='not_applicable'
    else:
        snapshot=assessment['evidence_snapshot']
        stale=False
        for document in snapshot['documents']:
            current=repo.get_project_document(ws.conn,document['id'])
            if not current or current['project_id']!=project_id or current['path']!=document['path'] or current['content'][:4000]!=document['content']:
                stale=True
                break
        live=llm is None or getattr(llm,'mode',None)=='live'
        from lodestar.agent.project_evidence import export_allowed
        project=repo.get_project(ws.conn,project_id)
        if stale:
            result['status']='needs_reassessment'
        elif live and (not export_allowed(ws.config,project) or any(d['path'] not in ws.config.project_model_allowed_paths for d in snapshot['documents'])):
            result['status']='needs_export_scope'
        elif live and assessment.get('mode')!='live':
            result['status']='needs_live_assessment'
        else:
            from lodestar.agent.project_plan import _generate
            context={'status':'ready','documents':snapshot['documents'],'papers':snapshot['papers'],
                     'missing':assessment.get('missing_evidence',[])}
            result=_generate(ws,llm,goal,project_id,context,assessment['assessment'])
    result.update(assessment_id=row['id'],read_id=row['read_id'],recommendation_id=recommendation_id,
                  applicability_review=assessment.get('semantic_review','required'))
    with ws.conn:
        cursor=ws.conn.execute('INSERT INTO paper_candidate_plans(recommendation_id,assessment_id,read_id,status,result,created_at) VALUES(?,?,?,?,?,?)',
            (recommendation_id,row['id'],row['read_id'],result['status'],json.dumps(result,ensure_ascii=False),
             datetime.now(timezone.utc).isoformat(timespec='seconds')))
    return {**result,'plan_id':cursor.lastrowid}


def plans(ws,project_id,recommendation_id):
    _recommendation(ws,project_id,recommendation_id)
    rows=ws.conn.execute('SELECT * FROM paper_candidate_plans WHERE recommendation_id=? ORDER BY id DESC LIMIT 20',
                         (recommendation_id,)).fetchall()
    return [{**dict(row),'result':json.loads(row['result'])} for row in rows]
