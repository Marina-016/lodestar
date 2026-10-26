"""Explicit, read-only GitHub snapshots. No clone, code execution or model call."""
import base64
import binascii
import json
import os
import re
from urllib.parse import quote, urlsplit

import requests

SCHEMA = '''
CREATE TABLE IF NOT EXISTS web_project_sources (
 project_id INTEGER PRIMARY KEY REFERENCES projects(id),
 repository TEXT NOT NULL UNIQUE COLLATE NOCASE, branch TEXT NOT NULL,
 commit_sha TEXT NOT NULL, readme_path TEXT NOT NULL, readme TEXT NOT NULL,
 truncated INTEGER NOT NULL DEFAULT 0, allow_model_readme INTEGER NOT NULL DEFAULT 0,
 synced_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS web_project_versions (
 id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL,
 branch TEXT NOT NULL, commit_sha TEXT NOT NULL, synced_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS web_project_revisions (
 project_id INTEGER PRIMARY KEY REFERENCES projects(id), revision INTEGER NOT NULL DEFAULT 1,
 allow_model_description INTEGER NOT NULL DEFAULT 0
);
'''
README_LIMIT = 12000


class RepositoryError(ValueError):
    """Safe, locally authored message; never pass upstream bodies to the UI."""


def repository_url(value):
    if not isinstance(value, str) or len(value) > 300:
        raise ValueError('请填写 GitHub 仓库链接')
    parsed = urlsplit(value.strip())
    if (parsed.scheme != 'https' or parsed.netloc != 'github.com'
            or parsed.query or parsed.fragment):
        raise ValueError('仅支持 https://github.com/所有者/仓库 链接')
    parts = parsed.path.strip('/').removesuffix('.git').split('/')
    if len(parts) != 2 or any(not re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', p)
                             or p in ('.','..') for p in parts):
        raise ValueError('请使用仓库首页链接，不是文件或分支链接')
    return 'https://github.com/' + '/'.join(parts)


def _get(session, path, *, params=None, optional=False, token=None):
    headers = {'Accept':'application/vnd.github+json', 'User-Agent':'Lodestar-projects',
               'X-GitHub-Api-Version':'2022-11-28'}
    token = token or os.getenv('GITHUB_TOKEN')
    if token:
        headers['Authorization'] = 'Bearer ' + token
    try:
        # Never follow a redirect carrying credentials or trust a returned URL.
        with session.get('https://api.github.com' + path, params=params, headers=headers,
                         timeout=(5,12), stream=True, allow_redirects=False) as response:
            if optional and response.status_code == 404:
                return None
            if response.status_code != 200:
                reason = {401:'GitHub 凭据无效',403:'GitHub 拒绝访问或请求已限流',
                          404:'仓库或分支不存在，或当前凭据无权读取'}.get(
                              response.status_code,'GitHub 请求失败，请稍后手动重试')
                raise RepositoryError(reason)
            body = bytearray()
            for chunk in response.iter_content(8192):
                body.extend(chunk)
                if len(body) > 1500000:
                    raise RepositoryError('GitHub 响应过大，本次未导入')
            value = json.loads(body)
            if not isinstance(value,dict):
                raise RepositoryError('GitHub 返回格式无效')
            return value
    except (requests.RequestException, ValueError) as error:
        if isinstance(error,RepositoryError):
            raise
        raise RepositoryError('GitHub 读取失败；未改动已保存资料，请检查网络和服务端凭据') from None


def fetch_snapshot(url, branch='', *, session=None):
    url = repository_url(url)
    if not isinstance(branch,str) or len(branch)>200 or any(ord(c)<32 for c in branch):
        raise ValueError('分支名称无效')
    owned = session is None
    session = session or requests.Session()
    try:
        from lodestar.project_workspaces import credential
        token = credential()
        path = '/repos/' + url.removeprefix('https://github.com/')
        meta = _get(session,path,token=token)
        branch = branch.strip() or meta.get('default_branch')
        if not isinstance(branch,str) or not branch or len(branch)>200:
            raise RepositoryError('仓库还没有可读取的默认分支')
        head = _get(session,path+'/commits/'+quote(branch,safe=''),token=token)
        sha = head.get('sha','')
        if not isinstance(sha,str) or not re.fullmatch('[0-9a-fA-F]{40,64}',sha):
            raise RepositoryError('未取得有效的 Git 提交版本')
        readme = _get(session,path+'/readme',params={'ref':sha},optional=True,token=token)
        text, readme_path, truncated = '', '', False
        if readme is not None:
            readme_path = readme.get('path','')
            if (not isinstance(readme_path,str) or len(readme_path)>500
                    or any(part in ('.','..') for part in readme_path.split('/'))
                    or readme_path.startswith('/') or '\\' in readme_path
                    or readme.get('type') != 'file' or readme.get('encoding') != 'base64'):
                raise RepositoryError('README 不是支持的普通文本文件')
            try:
                raw = base64.b64decode(''.join(readme['content'].split()),validate=True)
                decoded = raw.decode('utf-8')
            except (KeyError,ValueError,UnicodeError,binascii.Error):
                raise RepositoryError('README 编码无效，未改动已保存资料') from None
            if '\x00' in decoded:
                raise RepositoryError('README 不是可读文本')
            text, truncated = decoded[:README_LIMIT], len(decoded)>README_LIMIT
        return {'repository':url, 'name':str(meta.get('full_name') or url.split('github.com/')[1])[:80],
                'description':str(meta.get('description') or '')[:4000],
                'branch':branch, 'commit_sha':sha, 'readme_path':readme_path,
                'readme':text, 'truncated':truncated}
    finally:
        if owned:
            session.close()


def approved_readme(conn, project_id):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='web_project_sources'").fetchone():
        return None
    row = conn.execute('SELECT * FROM web_project_sources WHERE project_id=? AND allow_model_readme=1',
                       (project_id,)).fetchone()
    if not row or not row['readme']:
        return None
    return {'path':row['readme_path'], 'content':row['readme'][:4000],
            'url':row['repository']+'/blob/'+row['commit_sha']+'/'+quote(row['readme_path'],safe='/'),
            'commit_sha':row['commit_sha'], 'indexed_at':row['synced_at'],
            'truncated':bool(row['truncated']) or len(row['readme'])>4000,
            'source':'github_user_approved_snapshot'}


def approved_description(conn, project):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='web_project_revisions'").fetchone():
        return None
    row = conn.execute('SELECT allow_model_description FROM web_project_revisions WHERE project_id=?',
                       (project['id'],)).fetchone()
    if row and row[0]:
        return {'id':project['id'], 'name':project['name'][:80],
                'description':(project.get('description') or '')[:4000],
                'status':project['status'], 'source':'user_approved_project_description',
                'note':'User-managed background, not verified implementation evidence.'}
    return None
