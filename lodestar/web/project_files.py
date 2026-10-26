"""Explicit, version-bound file excerpts in owned project checkouts."""
from hashlib import sha256
from pathlib import PurePosixPath
from lodestar import project_workspaces as git
from lodestar.web import memory

SCHEMA = """CREATE TABLE IF NOT EXISTS web_project_files (
 project_id INTEGER NOT NULL, path TEXT NOT NULL, commit_sha TEXT NOT NULL,
 digest TEXT NOT NULL, content TEXT NOT NULL, truncated INTEGER NOT NULL,
 approved INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(project_id,path));"""
EXTENSIONS={'.py','.md','.txt','.js','.mjs','.ts','.tsx','.jsx','.json','.toml','.yaml','.yml','.rs','.go','.java','.css','.html'}


def root(ws, project_id):
    row=ws.conn.execute('SELECT directory FROM web_project_checkouts WHERE project_id=?',(project_id,)).fetchone()
    if not row: raise LookupError('此项目尚未建立 Git 工作区')
    return git.workspace_path(ws.config.workspace_dir,row[0])


def valid(path):
    if not isinstance(path,str) or len(path)>300 or '\\' in path or ':' in path: return False
    parts=PurePosixPath(path).parts
    return bool(parts and not path.startswith('/') and all(p not in ('..','.git') and not p.startswith('.') for p in parts)
        and PurePosixPath(path).suffix.lower() in EXTENSIONS
        and not any(s in path.lower() for s in ('credential','secret','id_rsa','private_key')))


def preview(ws,project_id,path):
    if not valid(path): raise ValueError('文件路径不允许预览或外发')
    base=root(ws,project_id)
    target=base/path
    if any(p.is_symlink() or getattr(p,'is_junction',lambda:False)() for p in [target,*target.parents] if p!=base.parent):
        raise ValueError('不读取符号链接或重解析目录')
    if not target.resolve().is_relative_to(base.resolve()): raise ValueError('文件超出工作区')
    git._run(base,'ls-files','--error-unmatch','--',path)
    if not target.is_file() or target.stat().st_size>200000: raise ValueError('仅支持200KB以内的已跟踪 UTF-8 文本文件')
    with target.open('rb') as f: raw=f.read(200001)
    if len(raw)>200000 or b'\x00' in raw: raise ValueError('文件过大或不是文本')
    try: text=raw.decode('utf-8')
    except UnicodeDecodeError: raise ValueError('文件不是 UTF-8 文本') from None
    return {'path':path,'digest':sha256(raw).hexdigest(),'commit_sha':git._run(base,'rev-parse','HEAD').strip(),
            'content':text[:4000],'truncated':len(text)>4000,'original_chars':len(text)}


def catalog(ws,project_id):
    base=root(ws,project_id)
    paths=[p for p in git._run(base,'ls-files','-z').split('\0') if valid(p)]
    saved={r['path']:dict(r) for r in ws.conn.execute('SELECT path,approved,commit_sha,digest FROM web_project_files WHERE project_id=?',(project_id,))}
    return {'files':[{'path':p,'approved':bool(saved.get(p,{}).get('approved'))} for p in paths[:2000]],
            'total':len(paths),'truncated':len(paths)>2000,'note':'批准绑定预览内容和Git提交；内容或HEAD变化后模型不再获得旧许可。'}


def save(ws,project_id,data):
    path=data.get('path')
    if not valid(path) or type(data.get('approved')) is not bool: raise ValueError('许可参数无效')
    memory.invalidate(ws,memory.affected(ws,{'scope':'project','project_id':project_id}))
    if not data['approved']:
        ws.conn.execute('UPDATE web_project_revisions SET revision=revision+1 WHERE project_id=?',(project_id,))
        ws.conn.execute('DELETE FROM web_project_files WHERE project_id=? AND path=?',(project_id,path))
        return {'approved':False}
    packet=preview(ws,project_id,path)
    if packet['digest']!=data.get('digest') or packet['commit_sha']!=data.get('commit_sha'):
        raise memory.MemoryBusy('文件版本已变化，请重新预览后授权')
    count=ws.conn.execute('SELECT COUNT(*) FROM web_project_files WHERE project_id=?',(project_id,)).fetchone()[0]
    if count>=50 and not ws.conn.execute('SELECT 1 FROM web_project_files WHERE project_id=? AND path=?',(project_id,path)).fetchone():
        raise ValueError('每项目最多授权50个文件')
    ws.conn.execute('INSERT OR REPLACE INTO web_project_files VALUES(?,?,?,?,?,?,1)',
        (project_id,path,packet['commit_sha'],packet['digest'],packet['content'],int(packet['truncated'])))
    ws.conn.execute('UPDATE web_project_revisions SET revision=revision+1 WHERE project_id=?',(project_id,))
    return {'approved':True}


def approved(ws,project_id,query='',limit=3):
    from lodestar.retrieval import terms
    # CLI databases predating the Web catalog have no such permissions.
    if not ws.conn.execute("SELECT 1 FROM sqlite_master WHERE name='web_project_files'").fetchone(): return []
    rows=[dict(r) for r in ws.conn.execute('SELECT * FROM web_project_files WHERE project_id=? AND approved=1 ORDER BY path',(project_id,))]
    wanted=terms(query)
    rows.sort(key=lambda r:-len(wanted & terms(r['path']+' '+r['content'])))
    output=[]
    for row in rows:
        if wanted and not wanted & terms(row['path']+' '+row['content']): continue
        try: current=preview(ws,project_id,row['path'])
        except (ValueError,LookupError,OSError,git.RepositoryError): continue
        if (current['digest'],current['commit_sha'])!=(row['digest'],row['commit_sha']): continue
        output.append({**current,'approval':'explicit_file_snapshot'})
        if len(output)>=limit: break
    return output
