"""Isolated no-network demonstration of candidate -> assessment -> plan -> A/B."""
import hashlib
import inspect
import json
from pathlib import Path

from lodestar.agent import candidate, candidate_experiment
from lodestar.agent.project_evidence import paper_context
from lodestar.config import Config
from lodestar.context import Workspace
from lodestar.llm import LLMClient
from lodestar.memory import repo
from lodestar.retrieval import select_excerpt


def _save(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')


def run(output):
    output=Path(output).resolve()
    output.mkdir(parents=True,exist_ok=False)
    cfg=Config(llm_mode='mock',search_mode='mock',model_calls_disabled=True,
               full_text_enabled=True,db_path=output/'demo.db',workspace_dir=output/'tasks')
    ws=Workspace(cfg)
    try:
        project=repo.upsert_project(ws.conn,'Pipeline fixture',status='active')
        path=Path(inspect.getsourcefile(paper_context))
        repo.replace_project_documents(ws.conn,project,[{'path':'lodestar/agent/project_evidence.py',
                                                        'content':path.read_text(encoding='utf-8')}])
        source={'title':'Functional fixture: source-location propagation',
                'url':'https://arxiv.org/abs/2601.00001','discovery_mode':'mock','source_type':'paper'}
        cursor=ws.conn.execute("""INSERT INTO paper_recommendations(project_id,paper_key,source,matches,first_seen,last_seen,channels)
            VALUES(?,?,?,?,?,?,?)""",(project,'fixture:arxiv:2601.00001',json.dumps(source),'[]','fixture','fixture','["recent"]'))
        recommendation=cursor.lastrowid;ws.conn.commit()
        raw=('Functional fixture: memory evidence must retain source offsets. '*60)
        excerpt=select_excerpt(raw,'memory evidence',3000)
        def reader(*args,**kwargs):
            return {'text':excerpt['text'],'read_depth':'full','coverage':excerpt['coverage'],
                    'evidence_spans':excerpt['evidence_spans'],'note':'Synthetic functional fixture, not a live paper read.'}
        read=candidate.read(ws,project,recommendation,reader=reader)
        client=LLMClient(cfg)
        assessment=candidate.assess(ws,client,project,recommendation,'memory evidence')
        plan=candidate.plan(ws,client,project,recommendation,'preserve source locations',assessment['assessment_id'])
        if not plan.get('contract_valid'):
            raise RuntimeError('Fixture plan did not satisfy the evidence contract')
        handoff=candidate.handoff(ws,project,recommendation,'demo-user')
        supplied=read['evidence']
        cases=[]
        for identity,spans in [('read-excerpt',supplied['evidence_spans']),
                ('single-span',[{'start':0,'end':40,'term_matches':1}]),
                ('two-spans',[{'start':0,'end':20,'term_matches':1},{'start':50,'end':70,'term_matches':2}]),
                ('empty-spans',[]),('abstract-control',[]),('missing-spans',None)]:
            paper={'url':source['url'],'content':supplied['content'],'read_depth':'abstract' if identity=='abstract-control' else 'full'}
            if spans is not None:
                paper['evidence_spans']=spans
            cases.append({'id':identity,'input':paper,'expected':spans or []})
        implementation=inspect.getsource(paper_context)
        protocol={'version':1,'hypothesis':'Retaining supplied location metadata keeps it available to downstream proposal consumers.',
            'dataset_kind':'functional_fixture','grader':'exact_output','min_delta':None,'seed':0,'cases':cases,
            'implementation_origin':{'path':'lodestar/agent/project_evidence.py','function':'paper_context',
                'sha256':hashlib.sha256(implementation.encode('utf-8')).hexdigest()},
            'baseline_scope':'Reproduction of historical paper-context fields without evidence_spans; not the whole historical agent.',
            'limits':['Metadata propagation only, not offset validity, semantic applicability, model quality or population gains.']}
        _save(output/'protocol.json',protocol)
        baseline=output/'baseline.py';arm=output/'candidate.py'
        baseline.write_text("import json,sys\ndata=json.load(sys.stdin)\nprint(json.dumps({'status':'complete','results':[{'case_id':c['id'],'output':[]} for c in data['cases']]}))\n",encoding='utf-8')
        arm.write_text("import json,sys\n"+implementation+"\ndata=json.load(sys.stdin)\nprint(json.dumps({'status':'complete','results':[{'case_id':c['id'],'output':paper_context(c['input'])['evidence_spans']} for c in data['cases']]}))\n",encoding='utf-8')
        experiment=candidate_experiment.execute(ws,project,recommendation,plan['plan_id'],output/'protocol.json',
                                                baseline,arm,output/'ab')
        for name,value in [('read',read),('assessment',assessment),('plan',plan),('handoff',handoff),('experiment',experiment)]:
            _save(output/(name+'.json'),value)
        count=ws.conn.execute('SELECT count(*) FROM learning_events').fetchone()[0]
        manifest={'mode':'offline_fixture','live':False,'model_calls':0,'project_id':project,
                  'recommendation_id':recommendation,'assessment_id':assessment['assessment_id'],'plan_id':plan['plan_id'],
                  'experiment_id':experiment['experiment_id'],'ab_verdict':experiment['verdict'],
                  'learning_events':count,'adoption_status':'not_decided',
                  'warning':'All paper/assessment/plan data are functional fixtures. A/B executes actual frozen code on fixture cases; no semantic or user mastery conclusion.'}
        _save(output/'manifest.json',manifest)
        return manifest
    finally:
        ws.close()
