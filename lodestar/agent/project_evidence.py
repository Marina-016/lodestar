"""Bound project context and enforce explicit export scope before model calls."""
from urllib.parse import urlparse

from lodestar.memory import repo


def repository_identity(url):
    parsed=urlparse(url or '')
    if parsed.scheme!='https' or parsed.hostname!='github.com':
        return None
    path=parsed.path.strip('/').removesuffix('.git').lower()
    return path if len(path.split('/'))==2 else None


def export_allowed(config, project):
    expected=repository_identity(config.project_model_allowed_repository)
    return bool(expected and expected==repository_identity(project.get('url'))
                and config.project_model_allowed_paths)


def collect(ws, goal, sources, project_id, *, live=False):
    project=repo.get_project(ws.conn,project_id)
    if project is None:
        raise ValueError('Unknown registered project')
    if live and not export_allowed(ws.config,project):
        return {'status':'needs_export_scope','documents':[],'papers':[],
                'missing':['No matching repository and explicit file export allowlist.']}
    allowed=set(ws.config.project_model_allowed_paths) if live else None
    # Search locally; only approved paths can enter a remote context.
    matches=repo.search_project_documents(ws.conn,goal,project_id=project_id,limit=20 if live else 3)
    documents=[]
    for match in matches:
        if allowed is not None and match['path'] not in allowed:
            continue
        document=repo.get_project_document(ws.conn,match['id'])
        documents.append({'path':document['path'],'content':document.get('content','')[:4000],
                          'id':document['id'],'indexed_at':document.get('indexed_at')})
        if len(documents)==3:
            break
    papers=[]
    for source in sources:
        if source.get('source_type','paper')!='paper' or source.get('read_error') or not source.get('content'):
            continue
        if live and source.get('read_mode')=='mock':
            continue
        papers.append({'url':source['url'],'content':source['content'][:10000],
                       'read_depth':source.get('read_depth'),'coverage':source.get('coverage'),
                       'evidence_spans':source.get('evidence_spans',[]),
                       'context_truncated':len(source['content'])>10000,
                       'span_coordinates':'Original extracted source offsets; not PDF page coordinates or semantic proof.'})
        if len(papers)==2:
            break
    missing=[]
    if not documents:
        missing.append('No relevant approved indexed project document.' if live else 'No relevant indexed project document.')
    if not any(p['read_depth']=='full' for p in papers):
        missing.append('No body-level paper excerpt; full method applicability is unverified.')
    for source in [*documents,*papers]:
        source['quote_candidates']=[line.strip()[:160] for line in source['content'].splitlines() if len(line.strip())>=40][:6]
    return {'status':'ready','documents':documents,'papers':papers,'missing':missing}
