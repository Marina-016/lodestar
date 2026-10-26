"""Project research inbox reusing canonical paper-watch and candidate records."""
from datetime import datetime,timezone
import json,re
from lodestar.memory import repo
from lodestar.agent import candidate

SCHEMA="""CREATE TABLE IF NOT EXISTS web_research_jobs (
 id TEXT PRIMARY KEY, project_id INTEGER NOT NULL, action TEXT NOT NULL,
 status TEXT NOT NULL, request_key TEXT NOT NULL, payload TEXT NOT NULL, config TEXT NOT NULL,
 result TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(project_id,request_key));
 CREATE TABLE IF NOT EXISTS web_plan_notes (
 id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, recommendation_id INTEGER NOT NULL,
 title TEXT NOT NULL, hypothesis TEXT NOT NULL, protocol TEXT NOT NULL,
 observation TEXT NOT NULL DEFAULT '', decision TEXT NOT NULL DEFAULT 'undecided',
 revision INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
"""


def project(ws,project_id):
    if type(project_id) is not int or not repo.get_project(ws.conn,project_id):raise LookupError('找不到项目')


def catalog(ws,project_id,state='',page=1):
    project(ws,project_id)
    if state not in ('','unread','read','ignored') or type(page) is not int or page<1:raise ValueError('筛选参数无效')
    where='project_id=?'+(' AND state=?' if state else '');args=[project_id,*([state] if state else [])]
    total=ws.conn.execute('SELECT COUNT(*) FROM paper_recommendations WHERE '+where,args).fetchone()[0]
    papers=[]
    for row in ws.conn.execute('SELECT * FROM paper_recommendations WHERE '+where+' ORDER BY last_seen DESC,id DESC LIMIT 40 OFFSET ?',[*args,(page-1)*40]):
        value=dict(row)
        for field in ('source','matches','channels'):value[field]=json.loads(value[field])
        evidence=candidate.evidence(ws,project_id,row['id'])
        value['reading']={'id':evidence['id'],'read_depth':evidence['evidence'].get('read_depth')} if evidence else None
        papers.append(value)
    watches=[{**dict(r),'terms':json.loads(r['terms'])} for r in ws.conn.execute('SELECT * FROM paper_watches WHERE project_id=? ORDER BY id DESC',(project_id,))]
    return {'papers':papers,'watches':watches,'total':total,'page':page,'jobs':[dict(r) for r in ws.conn.execute(
        'SELECT id,action,status,created_at,result FROM web_research_jobs WHERE project_id=? ORDER BY rowid DESC LIMIT 10',(project_id,))]}


def save_paper(ws,project_id,data):
    project(ws,project_id)
    url=data.get('url','');title=data.get('title','')
    match=re.fullmatch(r'https://arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5}(?:v\d+)?)(?:\.pdf)?',url)
    if not match or not isinstance(title,str) or len(title)>300:raise ValueError('请提供 arXiv 论文链接和300字符以内标题')
    url='https://arxiv.org/abs/'+match[1]
    key=('fixture:' if ws.config.search_mode=='mock' else '')+'arxiv:'+re.sub(r'v\d+$','',match[1])
    now=datetime.now(timezone.utc).isoformat()
    old=ws.conn.execute('SELECT id,source FROM paper_recommendations WHERE project_id=? AND paper_key=?',(project_id,key)).fetchone()
    if old:
        if json.loads(old['source'])['url']!=url:raise ValueError('已收藏该论文另一版本；保留旧证据，请通过订阅同步版本')
        return old['id']
    source={'url':url,'title':title.strip() or match[1],'source_type':'paper','read_depth':'metadata',
            'discovery_mode':ws.config.search_mode,'saved_by':'user'}
    return ws.conn.execute('INSERT INTO paper_recommendations(project_id,paper_key,source,matches,first_seen,last_seen,channels) VALUES(?,?,?,?,?,?,?)',
        (project_id,key,json.dumps(source,ensure_ascii=False),'[]',now,now,'["manual"]')).lastrowid


def update_state(ws,project_id,paper_id,state):
    candidate._recommendation(ws,project_id,paper_id)
    if state not in ('unread','read','ignored'):raise ValueError('阅读状态无效')
    ws.conn.execute('UPDATE paper_recommendations SET state=? WHERE id=? AND project_id=?',(state,paper_id,project_id))


def detail(ws,project_id,paper_id):
    paper=candidate._recommendation(ws,project_id,paper_id)
    reading=candidate.evidence(ws,project_id,paper_id)
    from lodestar.agent import candidate_experiment
    return {'paper':paper,'reading':reading,'plans':candidate.plans(ws,project_id,paper_id),
        'experiments':candidate_experiment.history(ws,project_id,paper_id),
        'investigations':candidate_experiment.investigations(ws,project_id,paper_id),
        'notes':[dict(r) for r in ws.conn.execute('SELECT * FROM web_plan_notes WHERE project_id=? AND recommendation_id=? ORDER BY id DESC',(project_id,paper_id))]}


def plan_note(ws,project_id,data):
    project(ws,project_id);paper_id=data.get('paper_id');candidate._recommendation(ws,project_id,paper_id)
    fields={k:data.get(k,'') for k in ('title','hypothesis','protocol','observation')}
    if any(not isinstance(v,str) or len(v)>6000 for v in fields.values()) or not all(fields[k].strip() for k in ('title','hypothesis','protocol')):
        raise ValueError('请填写标题、待验证假设及验证方法，每项最多6000字符')
    decision=data.get('decision','undecided')
    if decision not in ('undecided','investigate','adopt','reject'):raise ValueError('决策无效')
    note_id=data.get('note_id')
    if note_id:
        result=ws.conn.execute('UPDATE web_plan_notes SET title=?,hypothesis=?,protocol=?,observation=?,decision=?,revision=revision+1,updated_at=CURRENT_TIMESTAMP '
            'WHERE id=? AND project_id=? AND recommendation_id=? AND revision=?',
            (*fields.values(),decision,note_id,project_id,paper_id,data.get('revision')))
        if not result.rowcount:raise ValueError('记录已变化或不属于本项目，请刷新')
    else:
        note_id=ws.conn.execute('INSERT INTO web_plan_notes(project_id,recommendation_id,title,hypothesis,protocol,observation,decision) VALUES(?,?,?,?,?,?,?)',
            (project_id,paper_id,*fields.values(),decision)).lastrowid
    return {'id':note_id,'origin':'user_record','execution_status':'not_executed_by_lodestar','mastery_effect':'none'}
