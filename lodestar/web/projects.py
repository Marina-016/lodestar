"""Project catalog and user-managed repository versions; network is staged outside locks."""
from datetime import datetime, timezone

from lodestar.memory import repo
from lodestar.project_sources import SCHEMA, repository_url
from lodestar import project_workspaces
from lodestar.web import memory


def initialize(ws):
    ws.conn.executescript(SCHEMA)
    from lodestar.web.project_files import SCHEMA as FILE_SCHEMA
    ws.conn.executescript(FILE_SCHEMA)
    from lodestar.web.research import SCHEMA as RESEARCH_SCHEMA
    ws.conn.executescript(RESEARCH_SCHEMA)
    from lodestar.web.background import SCHEMA as BACKGROUND_SCHEMA
    ws.conn.executescript(BACKGROUND_SCHEMA)
    ws.conn.executescript(project_workspaces.SCHEMA)
    ws.conn.execute('INSERT OR IGNORE INTO web_project_revisions(project_id) SELECT id FROM projects')


def catalog(ws):
    result = []
    for project in repo.list_projects(ws.conn):
        source = ws.conn.execute('SELECT repository,branch,commit_sha,readme_path,truncated,allow_model_readme,synced_at '
                                 'FROM web_project_sources WHERE project_id=?',(project['id'],)).fetchone()
        revision = ws.conn.execute('SELECT revision,allow_model_description FROM web_project_revisions WHERE project_id=?',(project['id'],)).fetchone()
        result.append({**project,'revision':revision[0] if revision else 1,
                       'allow_model_description':bool(revision and revision[1]),
                       'has_workspace':bool(ws.conn.execute('SELECT 1 FROM web_project_checkouts WHERE project_id=?',(project['id'],)).fetchone()),
                       'repository':dict(source) if source else None,
                       'memory_count':ws.conn.execute("SELECT COUNT(*) FROM assistant_memories WHERE user_id=? AND project_id=? AND status='active'",
                                                     (memory.USER,project['id'])).fetchone()[0],
                       'task_count':ws.conn.execute("SELECT COUNT(*) FROM web_items WHERE owner='local' AND project_id=?",(project['id'],)).fetchone()[0]})
    return result


def detail(ws, project_id):
    project = next((p for p in catalog(ws) if p['id']==project_id),None)
    if not project:
        raise LookupError('找不到这个项目')
    row = ws.conn.execute('SELECT readme FROM web_project_sources WHERE project_id=?',(project_id,)).fetchone()
    checkout = ws.conn.execute('SELECT directory FROM web_project_checkouts WHERE project_id=?',(project_id,)).fetchone()
    workspace = None
    if checkout:
        try:
            workspace = project_workspaces.status(ws.config.workspace_dir,checkout[0])
        except (ValueError, project_workspaces.RepositoryError) as error:
            workspace = {'error':str(error)}
    return {**project,'readme':row[0] if row else '', 'workspace':workspace,
            'versions':[dict(r) for r in ws.conn.execute('SELECT branch,commit_sha,synced_at FROM web_project_versions '
                        'WHERE project_id=? ORDER BY id DESC LIMIT 20',(project_id,))],
            'tasks':[{'id':r['id'],'name':r['name'],'status':r['status']} for r in ws.conn.execute(
                "SELECT id,name,status FROM web_items WHERE owner='local' AND project_id=? ORDER BY created_at DESC",(project_id,))]}


def check_revision(ws, project_id, revision):
    row = ws.conn.execute('SELECT revision FROM web_project_revisions WHERE project_id=?',(project_id,)).fetchone()
    if not repo.get_project(ws.conn,project_id):
        raise LookupError('找不到这个项目')
    current = row[0] if row else 1
    if type(revision) is not int or revision!=current:
        raise memory.MemoryBusy('项目已更新，请刷新后再修改或同步')


def save(ws, data):
    project_id = data.get('id')
    name, description = data.get('name',''), data.get('description','')
    status = data.get('status','active')
    if (not isinstance(name,str) or not 1<=len(name.strip())<=80 or not isinstance(description,str)
            or len(description)>4000 or status not in repo.PROJECT_STATUS):
        raise ValueError('项目名称、说明或状态无效')
    old = None
    if project_id is not None:
        if type(project_id) is not int:
            raise ValueError('项目 ID 无效')
        check_revision(ws,project_id,data.get('revision'))
        old = repo.get_project(ws.conn,project_id)
        memory.invalidate(ws,memory.affected(ws,{'scope':'project','project_id':project_id}))
    if ws.conn.execute('SELECT 1 FROM projects WHERE name=? AND id<>?',(name.strip(),project_id or -1)).fetchone():
        raise memory.MemoryBusy('已有同名项目，请打开已有项目')
    now = datetime.now(timezone.utc).isoformat()
    if old:
        ws.conn.execute('UPDATE projects SET name=?,description=?,status=?,updated_at=? WHERE id=?',
                        (name.strip(),description.strip(),status,now,project_id))
        ws.conn.execute('INSERT INTO web_project_revisions(project_id,revision) VALUES(?,2) '
                        'ON CONFLICT(project_id) DO UPDATE SET revision=revision+1',(project_id,))
    else:
        project_id = ws.conn.execute('INSERT INTO projects(name,description,status,created_at,updated_at) VALUES(?,?,?,?,?)',
                        (name.strip(),description.strip(),status,now,now)).lastrowid
        ws.conn.execute('INSERT INTO web_project_revisions(project_id) VALUES(?)',(project_id,))
    if 'allow_model_readme' in data:
        if type(data['allow_model_readme']) is not bool:
            raise ValueError('资料许可无效')
        ws.conn.execute('UPDATE web_project_sources SET allow_model_readme=? WHERE project_id=?',
                        (int(data['allow_model_readme']),project_id))
    if 'allow_model_description' in data:
        if type(data['allow_model_description']) is not bool:
            raise ValueError('项目说明许可无效')
        ws.conn.execute('UPDATE web_project_revisions SET allow_model_description=? WHERE project_id=?',
                        (int(data['allow_model_description']),project_id))
    return project_id


def apply_snapshot(ws, snapshot, *, project_id=None, revision=None):
    now = datetime.now(timezone.utc).isoformat()
    if project_id is None:
        if ws.conn.execute('SELECT 1 FROM web_project_sources WHERE repository=? COLLATE NOCASE',
                           (snapshot['repository'],)).fetchone():
            raise memory.MemoryBusy('该仓库已导入，请打开项目后手动同步')
        project_id = save(ws,{'name':snapshot['name'],'description':snapshot['description']})
    else:
        check_revision(ws,project_id,revision)
        existing = ws.conn.execute('SELECT * FROM web_project_sources WHERE project_id=?',(project_id,)).fetchone()
        if not existing or repository_url(existing['repository']).lower()!=snapshot['repository'].lower():
            raise ValueError('同步仓库与项目不匹配')
        memory.invalidate(ws,memory.affected(ws,{'scope':'project','project_id':project_id}))
        ws.conn.execute('UPDATE web_project_revisions SET revision=revision+1 WHERE project_id=?',(project_id,))
    # A changed commit revokes prior snapshot approval; the user reviews it again.
    ws.conn.execute('INSERT INTO web_project_sources(project_id,repository,branch,commit_sha,readme_path,readme,truncated,synced_at) '
        'VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(project_id) DO UPDATE SET branch=excluded.branch,commit_sha=excluded.commit_sha,'
        'readme_path=excluded.readme_path,readme=excluded.readme,truncated=excluded.truncated,synced_at=excluded.synced_at,'
        'allow_model_readme=CASE WHEN web_project_sources.commit_sha=excluded.commit_sha '
        'AND web_project_sources.readme_path=excluded.readme_path AND web_project_sources.readme=excluded.readme '
        'THEN web_project_sources.allow_model_readme ELSE 0 END',
        (project_id,snapshot['repository'],snapshot['branch'],snapshot['commit_sha'],snapshot['readme_path'],
         snapshot['readme'],int(snapshot['truncated']),now))
    previous = ws.conn.execute('SELECT commit_sha,branch FROM web_project_versions WHERE project_id=? ORDER BY id DESC LIMIT 1',(project_id,)).fetchone()
    if not previous or (previous[0],previous[1])!=(snapshot['commit_sha'],snapshot['branch']):
        ws.conn.execute('INSERT INTO web_project_versions(project_id,branch,commit_sha,synced_at) VALUES(?,?,?,?)',
                        (project_id,snapshot['branch'],snapshot['commit_sha'],now))
    ws.conn.execute('UPDATE projects SET url=?,updated_at=? WHERE id=?',(snapshot['repository'],now,project_id))
    return project_id
