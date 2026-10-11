"""Persistent local reservations against a manually verified free search allowance.

This bounds Lodestar calls, not account-wide cloud billing. Never grant from a
published promotional total: verify the account's remaining quota first.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
from pathlib import Path
import sqlite3
import time


class QuotaUnavailable(ValueError):
    pass


def _scope(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def grant(path: Path, key: str, *, verified_remaining: int, call_limit: int,
          evidence: str, valid_for_s: int = 3600) -> None:
    """Operator-only receipt after checking this account; never called by an agent."""
    if (not key or type(verified_remaining) is not int or type(call_limit) is not int
            or not 1 <= call_limit <= verified_remaining <= 2000
            or not evidence.strip() or not 1 <= valid_for_s <= 86400):
        raise ValueError('需要已核实的剩余免费次数、调用上限、核对依据与有效期')
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=5)) as conn, conn:
        conn.execute('CREATE TABLE IF NOT EXISTS search_allowance ('
                     'scope TEXT PRIMARY KEY, remaining INTEGER NOT NULL, '
                     'verified_at REAL NOT NULL, expires_at REAL NOT NULL, '
                     'verified_remaining INTEGER NOT NULL, evidence TEXT NOT NULL)')
        now = time.time()
        conn.execute('INSERT OR REPLACE INTO search_allowance VALUES (?,?,?,?,?,?)',
                     (_scope(key), call_limit, now, now + valid_for_s,
                      verified_remaining, evidence))


def reserve(path: Path, key: str) -> None:
    """Debit before a network attempt, atomically across processes; no failure refunds."""
    if not path.is_file():
        raise QuotaUnavailable('联网搜索免费额度尚未核实，请先登录百炼核对剩余次数')
    try:
        with closing(sqlite3.connect(path, timeout=5)) as conn, conn:
            changed = conn.execute('UPDATE search_allowance SET remaining=remaining-1 '
                'WHERE scope=? AND remaining>0 AND expires_at>?', (_scope(key), time.time()))
            if changed.rowcount == 1:
                return
    except sqlite3.Error:
        raise QuotaUnavailable('搜索额度记录不可用，已停止调用') from None
    raise QuotaUnavailable('搜索免费调用上限已用尽、核对记录已过期或账号已变更，请重新核对')
