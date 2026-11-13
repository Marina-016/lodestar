"""Explicit local Markdown roots; no Obsidian plugin, cloud or implicit model access."""
from pathlib import Path,PurePosixPath
from hashlib import sha256
import os,uuid
from urllib.parse import quote
from lodestar.web import memory

SCHEMA='''CREATE TABLE IF NOT EXISTS web_project_vaults (
 project_id INTEGER PRIMARY KEY, root TEXT NOT NULL UNIQUE, kind TEXT NOT NULL DEFAULT 'local');'''

def linked(path):
    return path.is_symlink() or bool(getattr(path.lstat(),'st_file_attributes',0)&0x400)

def checked_root(value):
    if not isinstance(value,str) or not value.strip() or len(value)>1000:raise ValueError('请输入本地目录的完整路径')
    path=Path(value).expanduser()
    if not path.is_absolute() or str(path).startswith('\\\\') or not path.is_dir() or path==Path(path.anchor):raise ValueError('请选择一个已存在的本地笔记目录')
    if any(linked(p) for p in [path,*path.parents]):raise ValueError('不连接符号链接或重解析目录')
    return path.resolve()

def info(ws,project_id):
    row=ws.conn.execute('SELECT * FROM web_project_vaults WHERE project_id=?',(project_id,)).fetchone()
    return dict(row) if row else None

def connect(ws,data):
    from lodestar.web import projects
    root=checked_root(data.get('path'))
    if ws.conn.execute('SELECT 1 FROM web_project_vaults WHERE root=?',(str(root),)).fetchone():raise memory.MemoryBusy('这个目录已连接')
    project=projects.save(ws,{'name':data.get('name') or root.name,'description':data.get('description','')})
    kind='obsidian' if (root/'.obsidian').is_dir() else 'local'
    ws.conn.execute('INSERT INTO web_project_vaults VALUES(?,?,?)',(project,str(root),kind))
    return project

def root(ws,project_id):
    value=info(ws,project_id)
    if not value:raise LookupError('此项目没有连接本地目录')
    return checked_root(value['root'])

def target(ws,project_id,path):
    from lodestar.web.project_files import valid
    if not valid(path) or PurePosixPath(path).suffix.lower()!='.md':raise ValueError('仅支持目录内的 Markdown 笔记')
    base=root(ws,project_id);out=base/path
    if not out.resolve().is_relative_to(base):raise ValueError('笔记路径超出目录')
    if any(linked(p) for p in [out,*out.parents] if p.exists()):raise ValueError('不读取链接文件或重解析目录')
    return out

def read(ws,project_id,path):
    file=target(ws,project_id,path)
    if not file.is_file() or file.stat().st_size>200000:raise ValueError('仅支持 200KB 以内的 UTF-8 笔记')
    raw=file.read_bytes()
    if len(raw)>200000 or b'\0' in raw:raise ValueError('笔记过大或不是文本')
    try:text=raw.decode('utf-8-sig')
    except UnicodeDecodeError:raise ValueError('笔记需要使用 UTF-8 编码') from None
    uri='obsidian://open?path='+quote(str(file.resolve()),safe='') if info(ws,project_id)['kind']=='obsidian' else None
    return {'open_uri':uri,'path':path,'content':text,'digest':sha256(raw).hexdigest(),'commit_sha':'local','original_chars':len(text),'truncated':False}

def catalog(ws,project_id,query=''):
    from lodestar.web.project_files import valid
    base=root(ws,project_id);output=[];seen=0;truncated=False;query=str(query).strip().lower()[:200];read_budget=1000000
    for directory,dirs,files in os.walk(base,followlinks=False):
        dirs[:]=sorted(d for d in dirs if not d.startswith('.') and not linked(Path(directory)/d))
        for name in sorted(files):
            seen+=1
            if seen>20000:truncated=True;break
            path=(Path(directory)/name).relative_to(base).as_posix()
            if not valid(path) or PurePosixPath(path).suffix.lower()!='.md':continue
            try:
                file=target(ws,project_id,path)
                if not file.is_file():continue
                if query and query not in path.lower():
                    if read_budget<=0:truncated=True;continue
                    raw=file.open('rb')
                    with raw:excerpt=raw.read(min(read_budget,16000))
                    read_budget-=len(excerpt)
                    if query not in excerpt.decode('utf-8',errors='replace').lower():continue
                output.append({'path':path})
            except (ValueError,OSError):continue
            if len(output)>=2000:truncated=True;break
        if truncated and (seen>20000 or len(output)>=2000):break
    return {'files':output,'total':len(output),'truncated':truncated}

def save(ws,project_id,data):
    from lodestar.web import projects
    projects.check_revision(ws,project_id,data.get('revision'))
    text=data.get('content');path=data.get('path');digest=data.get('digest','')
    if not isinstance(text,str) or len(text.encode('utf-8'))>200000 or '\0' in text:raise ValueError('笔记需为 200KB 以内的文字')
    file=target(ws,project_id,path);old=read(ws,project_id,path) if file.exists() else None
    if (old and old['digest']!=digest) or (not old and digest):raise memory.MemoryBusy('笔记已在其他地方修改，请重新打开后再保存')
    memory.invalidate(ws,memory.affected(ws,{'scope':'project','project_id':project_id}))
    file.parent.mkdir(parents=True,exist_ok=True)
    if old:
        backup=ws.config.workspace_dir/'vault-backups'/str(project_id);backup.mkdir(parents=True,exist_ok=True)
        (backup/(uuid.uuid4().hex+'.md')).write_bytes(file.read_bytes())
    temporary=file.parent/('.lodestar-'+uuid.uuid4().hex+'.tmp')
    try:
        temporary.write_text(text,encoding='utf-8',newline='')
        # Recheck the actual target immediately before atomic replacement.
        target(ws,project_id,path)
        if old and sha256(file.read_bytes()).hexdigest()!=digest:raise memory.MemoryBusy('笔记已变化，请重新打开')
        if not old and file.exists():raise memory.MemoryBusy('笔记已存在，请重新打开')
        os.replace(temporary,file)
    finally:
        temporary.unlink(missing_ok=True)
    ws.conn.execute('DELETE FROM web_project_files WHERE project_id=? AND path=?',(project_id,path))
    ws.conn.execute('UPDATE web_project_revisions SET revision=revision+1 WHERE project_id=?',(project_id,))
    return read(ws,project_id,path)
