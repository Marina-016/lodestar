"""Observed model availability, not a billing meter or an automatic probe loop."""
from dataclasses import replace
import hashlib
import json
import os
import time

from lodestar.chat_settings import snapshot

SCHEMA = '''
CREATE TABLE IF NOT EXISTS web_model_health (
 service TEXT NOT NULL, model TEXT NOT NULL, request TEXT NOT NULL,
 status TEXT NOT NULL, observed_at REAL NOT NULL,
 PRIMARY KEY(service,model,request)
);
'''
BLOCKED = {'quota_exhausted', 'model_unavailable', 'request_rejected'}
LABELS = {'quota_exhausted': '免费额度已耗尽', 'model_unavailable': '模型不存在或不可用',
          'request_rejected': '此请求配置被服务拒绝'}
FRESH_SECONDS = 24 * 3600


def service_key(cfg):
    """Separate providers, endpoints, accounts and mock; never persist credentials."""
    if cfg.llm_provider == 'dashscope':
        endpoint, credentials = cfg.llm_base_url, [os.getenv('DASHSCOPE_API_KEY', '')]
    else:
        endpoint = cfg.llm_base_url or os.getenv('ANTHROPIC_BASE_URL', 'https://api.anthropic.com')
        credentials = [os.getenv('ANTHROPIC_API_KEY', ''), os.getenv('ANTHROPIC_AUTH_TOKEN', '')]
    value = [cfg.llm_mode, cfg.llm_provider, endpoint.rstrip('/'), credentials]
    return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode()).hexdigest()


def request_key(cfg):
    values = snapshot(cfg)
    for key in ('model', 'profile', 'operation_budget'):
        values.pop(key)
    values['temperature'] = cfg.temperature
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def record(conn, cfg, status, *, observed_at=None):
    """Failures are model-wide only for explicit quota/model-unavailable signals."""
    if cfg.llm_mode != 'live' or status not in BLOCKED | {'available', 'unknown'}:
        return
    when = time.time() if observed_at is None else observed_at
    scope = service_key(cfg)
    key = '*' if status in {'quota_exhausted', 'model_unavailable'} else request_key(cfg)
    fence = conn.execute("SELECT MAX(observed_at) FROM web_model_health WHERE service=? AND model=? AND status='unknown'",
                         (scope,cfg.model)).fetchone()[0]
    if fence is not None and fence > when:
        return
    if key == '*':
        latest = conn.execute('SELECT MAX(observed_at) FROM web_model_health WHERE service=? AND model=?',
                              (scope,cfg.model)).fetchone()[0]
        if latest is not None and latest > when:
            return
    if status == 'available':
        blocked = conn.execute('SELECT observed_at FROM web_model_health WHERE service=? AND model=? AND request=?',
                               (scope, cfg.model, '*')).fetchone()
        if blocked and blocked[0] >= when:
            return  # An older concurrent success cannot revive an exhausted model.
        conn.execute('DELETE FROM web_model_health WHERE service=? AND model=? AND request=? AND observed_at<?',
                     (scope, cfg.model, '*', when))
    conn.execute('INSERT INTO web_model_health(service,model,request,status,observed_at) VALUES(?,?,?,?,?) '
        'ON CONFLICT(service,model,request) DO UPDATE SET status=excluded.status,observed_at=excluded.observed_at '
        'WHERE excluded.observed_at>=web_model_health.observed_at', (scope,cfg.model,key,status,when))


def restore(conn, cfg, model):
    """Explicit user acknowledgement only; never declare restored quota or infer."""
    if not isinstance(model, str) or not model or len(model) > 200 or any(c.isspace() for c in model):
        raise ValueError('模型 ID 无效')
    conn.execute('DELETE FROM web_model_health WHERE service=? AND model=?', (service_key(cfg),model))
    record(conn, replace(cfg, model=model), 'unknown')


def observe(conn, cfg, result, llm, started_at):
    diagnostic = result.get('model_error') or {}
    code = diagnostic.get('code')
    if code in {'quota_exhausted', 'model_unavailable'}:
        record(conn, cfg, code, observed_at=started_at)
    elif code == 'http_error' and diagnostic.get('http_status') in (400,404,422):
        record(conn, cfg, 'request_rejected', observed_at=started_at)
    elif result.get('status') not in ('error','cancelled') and isinstance(
            getattr(llm, 'last_dialogue_observation', None), dict):
        observation = llm.last_dialogue_observation
        if observation.get('requested_model') == cfg.model:
            record(conn, cfg, 'available', observed_at=started_at)


def view(conn, cfg, candidates, *, last_run=None, now=None):
    now = time.time() if now is None else now
    rows = conn.execute('SELECT model,request,status,observed_at FROM web_model_health WHERE service=?',
                        (service_key(cfg),)).fetchall()
    by_model = {}
    for row in rows:
        by_model.setdefault(row[0], {})[row[1]] = (row[2], row[3])
    current_request = request_key(cfg)
    # Pre-upgrade errors have no account fingerprint. Use only this task's
    # matching latest configuration as a warning, never a global quota verdict.
    legacy_rejected = False
    if cfg.llm_mode == 'live' and last_run and cfg.model not in by_model:
        saved = json.loads(last_run['config'])
        result = json.loads(last_run['result'])
        diagnostic = result.get('model_error') or {}
        legacy_rejected = (saved == snapshot(cfg) and last_run['status'] == 'error'
            and diagnostic.get('code') == 'http_error' and diagnostic.get('http_status') in (400,404,422))
    models, statuses, unavailable, successes = [], {}, [], []
    candidates = list(dict.fromkeys([*candidates, *by_model]))
    for model in candidates:
        if not isinstance(model, str) or not model or len(model)>200 or any(c.isspace() for c in model):
            continue
        records = by_model.get(model, {})
        observed = records.get('*') or records.get(current_request)
        status, when = observed or ('unknown', 0)
        if model == cfg.model and legacy_rejected:
            status = 'request_rejected'
        if status in BLOCKED:
            unavailable.append({'model':model,'code':status,'reason':LABELS[status]})
            continue
        fresh = status == 'available' and 0 <= now-when <= FRESH_SECONDS
        models.append(model)
        statuses[model] = 'recently_available' if fresh else 'unverified'
        if fresh:
            successes.append((when,model))
    recommendation = (cfg.model if statuses.get(cfg.model) == 'recently_available' else
                      max(successes)[1] if successes else None)
    return {'models':models,'availability':statuses,'unavailable':unavailable,
            'current_blocked':cfg.model not in models, 'recommended_model':recommendation,
            'note':'最近成功不代表免费余额；未验证项不会自动调用。'}
