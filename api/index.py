"""Vercel entrypoint for the deterministic Lodestar portfolio demo.

The deployed demo deliberately uses curated replay data. Runtime state lives in
/tmp because Vercel functions have an ephemeral, writable filesystem there.
"""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("LODESTAR_SERVERLESS", "1")
os.environ.setdefault("LODESTAR_DEMO_REPLAY", "true")
os.environ.setdefault("LODESTAR_LLM_MODE", "mock")
os.environ.setdefault("LODESTAR_SEARCH_MODE", "mock")
os.environ.setdefault("LODESTAR_DB_PATH", "/tmp/lodestar/lodestar.db")
os.environ.setdefault("LODESTAR_WORKSPACE_DIR", "/tmp/lodestar/workspace")

from lodestar.config import load_config
from lodestar.demo import seed_demo
from lodestar.ui import Handler as _Handler


_cfg = load_config()
_seed_marker = _cfg.workspace_dir.parent / ".demo-seeded"
if not _seed_marker.exists():
    seed_demo(_cfg, clean=False)
    _seed_marker.parent.mkdir(parents=True, exist_ok=True)
    _seed_marker.touch()


class handler(_Handler):
    """Expose the existing stdlib HTTP handler through Vercel Python."""

    pass
