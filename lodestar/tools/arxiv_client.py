"""One cross-process gate, cooldown and metadata cache for the arXiv API."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import xml.etree.ElementTree as ET

import requests

API_URL = 'https://export.arxiv.org/api/query'
INTERVAL = 3.1
CACHE_TTL = 900


class ArxivUnavailable(requests.RequestException):
    def __init__(self, message, retry_after, failure_kind):
        super().__init__(message)
        self.retry_after = retry_after
        self.failure_kind = failure_kind


@dataclass(frozen=True)
class Feed:
    text: str
    fetched_at: str
    cache_hit: bool


def _retry_delay(value, now):
    try:
        return max(3.1, float(value))
    except (ValueError, TypeError):
        try:
            return max(3.1, parsedate_to_datetime(value).timestamp() - now)
        except (ValueError, TypeError, OverflowError):
            return 60.0


class ArxivClient:
    def __init__(self, path, *, clock=time.time, sleep=time.sleep):
        self.path = Path(path)
        self.clock, self.sleep = clock, sleep

    def query(self, params, timeout=30):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()
        conn = sqlite3.connect(self.path, timeout=60)
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS cache(key TEXT PRIMARY KEY, fetched REAL, body TEXT);
                CREATE TABLE IF NOT EXISTS state(id INTEGER PRIMARY KEY, last_start REAL, cooldown REAL, kind TEXT);
                INSERT OR IGNORE INTO state VALUES(1,0,0,'provider_unavailable');
                CREATE TABLE IF NOT EXISTS requests(id INTEGER PRIMARY KEY, started REAL, elapsed REAL,
                    status INTEGER, outcome TEXT, query TEXT);
            """)
            # Cached metadata is safe to serve during cooldown. A SQLite write
            # transaction serializes misses across terminals, including the HTTP call.
            conn.execute('BEGIN IMMEDIATE')
            cached = conn.execute('SELECT fetched,body FROM cache WHERE key=?', (key,)).fetchone()
            now = self.clock()
            if cached and 0 <= now - cached[0] < CACHE_TTL:
                return Feed(cached[1], datetime.fromtimestamp(cached[0], timezone.utc).isoformat(), True)
            last, cooldown, kind = conn.execute('SELECT last_start,cooldown,kind FROM state WHERE id=1').fetchone()
            if cooldown > now:
                raise ArxivUnavailable('arXiv API cooling down; use another source', round(cooldown-now, 1), kind)
            self.sleep(max(0, INTERVAL-(now-last)))
            started = self.clock()
            conn.execute('UPDATE state SET last_start=? WHERE id=1', (started,))
            status, outcome = None, 'error'
            try:
                response = requests.get(API_URL, params=params,
                    timeout=(min(8, timeout), timeout),
                    headers={'User-Agent': 'Lodestar/0.1 (personal research workspace)'})
                status = response.status_code
                if status == 429 or status >= 500:
                    delay = _retry_delay(response.headers.get('Retry-After'), self.clock())
                    kind = 'rate_limited' if status == 429 else 'provider_unavailable'
                    conn.execute('UPDATE state SET cooldown=?,kind=? WHERE id=1', (self.clock()+delay, kind))
                    raise ArxivUnavailable(f'arXiv HTTP {status}; cooling down', delay, kind)
                response.raise_for_status()
                root = ET.fromstring(response.text)
                if root.tag != '{http://www.w3.org/2005/Atom}feed':
                    raise ValueError('arXiv did not return an Atom feed')
                if root.find("{http://www.w3.org/2005/Atom}entry/{http://www.w3.org/2005/Atom}id") is not None:
                    if any('/api/errors' in (e.text or '') for e in root.findall('.//{http://www.w3.org/2005/Atom}id')):
                        raise ValueError('arXiv rejected the query')
                fetched = self.clock()
                conn.execute('INSERT OR REPLACE INTO cache VALUES(?,?,?)', (key, fetched, response.text))
                conn.execute('DELETE FROM cache WHERE fetched<?', (fetched-86400,))
                outcome = 'ok'
                return Feed(response.text, datetime.fromtimestamp(fetched, timezone.utc).isoformat(), False)
            except (requests.Timeout, requests.ConnectionError) as exc:
                conn.execute('UPDATE state SET cooldown=?,kind=? WHERE id=1', (self.clock()+10, 'provider_unavailable'))
                raise ArxivUnavailable(f'arXiv {type(exc).__name__}; cooling down', 10, 'provider_unavailable') from exc
            finally:
                conn.execute('INSERT INTO requests(started,elapsed,status,outcome,query) VALUES(?,?,?,?,?)',
                    (started, self.clock()-started, status, outcome, json.dumps(params, sort_keys=True)))
                conn.execute('DELETE FROM requests WHERE id NOT IN (SELECT id FROM requests ORDER BY id DESC LIMIT 200)')
        finally:
            conn.commit()
            conn.close()


def query(params, timeout=30):
    from lodestar.config import WORKSPACE_DIR
    root = Path(os.getenv('LODESTAR_ARXIV_CACHE_DIR', str(WORKSPACE_DIR / 'arxiv-client')))
    return ArxivClient(root / 'metadata.sqlite').query(params, timeout)
