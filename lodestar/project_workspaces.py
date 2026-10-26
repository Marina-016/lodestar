"""Owned local Git checkouts. Never run repository code, hooks, or push changes."""
import base64
import os
from pathlib import Path
import re
import subprocess
import uuid

from lodestar.project_sources import RepositoryError, repository_url

SCHEMA = '''CREATE TABLE IF NOT EXISTS web_project_checkouts (
 project_id INTEGER PRIMARY KEY REFERENCES projects(id), directory TEXT NOT NULL UNIQUE
);'''


def credential():
    if os.getenv('GITHUB_TOKEN'):
        return os.environ['GITHUB_TOKEN']
    env = dict(os.environ, GIT_TERMINAL_PROMPT='0', GCM_INTERACTIVE='Never')
    try:
        value = subprocess.run(['git','credential','fill'],
            input='protocol=https\nhost=github.com\n\n',text=True,encoding='utf-8',
            capture_output=True,timeout=10,env=env,check=False)
        if value.returncode==0:
            return next((line.split('=',1)[1] for line in value.stdout.splitlines()
                         if line.startswith('password=')),None)
    except (OSError,subprocess.SubprocessError):
        pass
    return None


def _run(directory, *args, network=False, token=None):
    env = {k:v for k,v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL=os.devnull,
               GIT_TERMINAL_PROMPT='0',GCM_INTERACTIVE='Never',GIT_LFS_SKIP_SMUDGE='1')
    if network and token:
        value = base64.b64encode(('x-access-token:'+token).encode()).decode()
        env.update(GIT_CONFIG_COUNT='1',GIT_CONFIG_KEY_0='http.https://github.com/.extraheader',
                   GIT_CONFIG_VALUE_0='Authorization: Basic '+value)
    command = ['git','-c','core.hooksPath='+os.devnull,'-c','core.fsmonitor=false',
               '-c','core.protectNTFS=true','-c','protocol.ext.allow=never','-c','protocol.file.allow=never',
               '-c','credential.helper=','-c','filter.lfs.required=false',
               '-c','filter.lfs.smudge=','-c','filter.lfs.process=']
    if directory is not None:
        command += ['-C',str(directory)]
    try:
        result = subprocess.run([*command,*args],capture_output=True,encoding='utf-8',
                                errors='replace',env=env,timeout=60 if network else 15,check=False)
    except (OSError,subprocess.SubprocessError):
        raise RepositoryError('Git 操作失败或超时；工作区保留，请检查 Git、网络及凭据后手动重试') from None
    if result.returncode:
        # Git stderr can contain credentials, private paths and remote content.
        raise RepositoryError('Git 操作未完成；没有自动重试或强制覆盖。请在本地工作区检查分支、权限和未提交改动')
    return result.stdout


def workspace_path(root, key):
    if not isinstance(key,str) or not re.fullmatch('[0-9a-f]{32}',key):
        raise ValueError('工作区标识无效')
    base = (Path(root)/'repositories').resolve()
    candidate = base/key
    if candidate.is_symlink() or candidate.resolve().parent!=base:
        raise ValueError('工作区路径不在受管理目录中')
    return candidate


def clone(root, snapshot):
    url = repository_url(snapshot['repository'])
    key = uuid.uuid4().hex
    path = workspace_path(root,key)
    path.parent.mkdir(parents=True,exist_ok=True)
    _run(None,'clone','--quiet','--no-checkout','--single-branch','--depth','50',
         '--branch',snapshot['branch'],'--',url,str(path),network=True,token=credential())
    # This directory was just created by us; no existing checkout is reset.
    _run(path,'checkout','--quiet','-B',snapshot['branch'],snapshot['commit_sha'])
    _run(path,'config','--local','core.hooksPath',os.devnull)
    _run(path,'config','--local','core.fsmonitor','false')
    return key


def status(root, key, *, history=True):
    path = workspace_path(root,key)
    if not path.is_dir() or not (path/'.git').is_dir():
        raise RepositoryError('本地 Git 工作区不存在，未自动重新克隆')
    changes = _run(path,'status','--porcelain=v1','--untracked-files=normal')
    branch = _run(path,'branch','--show-current').strip()
    sha = _run(path,'rev-parse','HEAD').strip()
    commits = []
    if history:
        for line in _run(path,'log','-8','--format=%H%x09%cs%x09%s').splitlines():
            parts = line.split('\t',2)
            if len(parts)==3:
                commits.append({'sha':parts[0],'date':parts[1],'subject':parts[2][:300]})
    return {'path':str(path),'branch':branch,'commit_sha':sha,'dirty':bool(changes),
            'changed_entries':len(changes.splitlines()),'commits':commits}


def sync(root, key, snapshot):
    path = workspace_path(root,key)
    current = status(root,key,history=False)
    if current['dirty']:
        raise RepositoryError('工作区有未提交改动或未跟踪文件；请先提交或自行整理，本次未拉取或覆盖')
    if current['branch']!=snapshot['branch']:
        raise RepositoryError('本地已切换分支；请先切回项目记录的分支，本次未自动切换')
    remote = _run(path,'remote','get-url','origin').strip()
    if repository_url(remote).lower()!=repository_url(snapshot['repository']).lower():
        raise RepositoryError('工作区 origin 与项目不一致，本次未同步')
    _run(path,'fetch','--quiet','--no-tags','origin',snapshot['branch'],network=True,token=credential())
    fetched = _run(path,'rev-parse','FETCH_HEAD').strip()
    if fetched!=snapshot['commit_sha']:
        raise RepositoryError('远端在同步期间更新；已读取远端但未合并，请刷新后手动再同步')
    # Ordinary ff-only merge preserves local commits and refuses divergence.
    _run(path,'merge','--quiet','--ff-only','FETCH_HEAD')
    return status(root,key)
