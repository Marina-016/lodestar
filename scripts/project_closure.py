"""Synthetic project fixtures for Q06 and bounded proposal acceptance only."""
import json
from lodestar import project_workspaces as git
from lodestar.web import project_files


def prepare(app):
    p = app.save_project({'name': 'Synthetic local research', 'description': 'Synthetic fixture'})
    q = app.save_project({'name': 'Synthetic voice prototype', 'description': 'Synthetic fixture'})
    p1, p2, q1 = app.create('P1', p), app.create('P2', p), app.create('Q1', q)
    app.note('main', {'scope':'personal', 'content':'PERSONAL_ORDER: 先解释机制，再给结论。'})
    app.note(p1, {'scope':'project', 'content':'PROJECT_P_ONLY: 当前只验证本地单用户，不做公网。'})
    app.note(q1, {'scope':'project', 'content':'PROJECT_Q_ONLY: 本轮只做语音输入原型。'})
    app.note(p1, {'scope':'task', 'content':'TASK_P1_ONLY: 本任务暂不比较向量数据库。'})
    key = 'e' * 32
    path = git.workspace_path(app.config.workspace_dir, key)
    path.mkdir(parents=True)
    for args in [('init','-q','-b','master'), ('config','user.name','Fixture'),
                 ('config','user.email','fixture@example.invalid')]:
        git._run(path, *args)
    (path/'memory.md').write_text(
        '# Synthetic project memory\nAPPROVED_MEMORY_DOC\n'
        '本地单用户 SQLite 研究助手，memory 使用字面词法检索。\n'
        '当前基线：固定20条合成问题，记录召回率、误召回、延迟和输入token；尚未测量效果。\n'
        '每次最多取2条记忆，不自动判定掌握，不自动执行代码，不做公网。\n', encoding='utf-8')
    (path/'private.md').write_text('UNAPPROVED_FILE_CANARY', encoding='utf-8')
    git._run(path, 'add', 'memory.md', 'private.md')
    git._run(path, 'commit', '-qm', 'Synthetic fixture')
    with app.workspace() as ws, ws.conn:
        ws.conn.execute('INSERT INTO web_project_checkouts VALUES(?,?)', (p,key))
        packet = project_files.preview(ws,p,'memory.md')
        project_files.save(ws,p,{**packet,'approved':True})
    return {'p':p,'q':q,'p1':p1,'p2':p2,'q1':q1}


def exercise(app, turn, report, observations, sources):
    fixture = prepare(app)
    report['project_fixture'] = fixture
    report['quality_cards'] = ['Q06', 'project-proposal']
    report['semantic_review'] = 'pending'
    report['scope'] += '; synthetic projects, no real repository export or experiment execution'
    papers = '；'.join(s['title']+' '+s['url'] for s in sources.candidates()[:2])
    turn('project-p', '这是隔离合成项目。读取获准的 memory.md，并通过论文工具读取以下冻结材料的方法与实验正文：' + papers +
         '。材料中的虚构标记或历史日期保持原样，不声称最新。按我的研究偏好和当前项目约束，建议先验证什么？'
         '说明材料支持什么、哪些只是项目假设，给一个保持其余条件不变的最小对照实验。'
         '不要执行代码，不要把虚构结果当现实论文收益。', fixture['p2'])
    turn('project-q', '按我的研究偏好和当前项目约束，建议先验证什么？只用当前上下文，不检索。', fixture['q1'])
    turn('project-local', '按我的研究偏好和当前项目约束，这个任务还有什么局部限制？只用当前上下文，不检索。', fixture['p1'])
    with app.workspace() as ws, ws.conn:
        project_files.save(ws, fixture['p'], {'path':'memory.md','approved':False})
    turn('project-revoked', '再次读取项目材料，当前还有可用的已授权文件吗？不要沿用已失效快照，不要搜索论文。', fixture['p2'])
    expected = {'project-p': ('PERSONAL_ORDER','PROJECT_P_ONLY'),
                'project-q': ('PERSONAL_ORDER','PROJECT_Q_ONLY'),
                'project-local': ('PERSONAL_ORDER','PROJECT_P_ONLY','TASK_P1_ONLY')}
    forbidden = {'project-p':('PROJECT_Q_ONLY','TASK_P1_ONLY','UNAPPROVED_FILE_CANARY'),
                 'project-q':('PROJECT_P_ONLY','TASK_P1_ONLY','APPROVED_MEMORY_DOC','UNAPPROVED_FILE_CANARY'),
                 'project-local':('PROJECT_Q_ONLY','UNAPPROVED_FILE_CANARY'),
                 'project-revoked':('APPROVED_MEMORY_DOC','UNAPPROVED_FILE_CANARY')}
    for phase, markers in expected.items():
        packets = [o['payload'] for o in observations if o['phase']==phase]
        report['checks'][phase+'_expected_context'] = bool(packets) and all(m in packets[0] for m in markers)
    for phase, markers in forbidden.items():
        packets = [o['payload'] for o in observations if o['phase']==phase]
        report['checks'][phase+'_no_forbidden_context'] = bool(packets) and all(not any(m in packet for m in markers) for packet in packets)
    packet_text = '\n'.join(o['payload'] for o in observations if o['phase']=='project-p')
    report['checks']['approved_file_actually_delivered'] = 'APPROVED_MEMORY_DOC' in packet_text
    actions = report['turns'][0]['actions']
    requested = {s['url'] for s in sources.candidates()[:2]}
    observed = {a['params'].get('url') for a in actions if a['action']=='read_paper'
                and a.get('read_depth')=='full' and not a['error']
                and a.get('query_matched') is not False and a.get('returned_text_chars',0)>0}
    report['checks']['proposal_body_actually_read'] = bool(requested) and requested <= observed
    with app.workspace() as ws:
        report['checks']['proposal_no_experiment_or_mastery'] = (
            ws.conn.execute('SELECT COUNT(*) FROM paper_candidate_experiments').fetchone()[0]==0 and
            ws.conn.execute('SELECT COUNT(*) FROM learning_events').fetchone()[0]==0)
