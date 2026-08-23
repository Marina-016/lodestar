"""Bounded repository ingestion for project-grounded Agent retrieval."""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote

import requests

from lodestar.project import GITHUB_API, _UA, _github_token, parse_github_url

TEXT_EXTENSIONS = {".py", ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".sql", ".sh"}
SKIP_PARTS = {".git", ".venv", "venv", "node_modules", "dist", "build", "__pycache__", ".pytest_cache", ".mypy_cache"}
MAX_FILES = 60
MAX_CHARS_PER_FILE = 24_000


def _allowed(relative: Path) -> bool:
    return relative.suffix.lower() in TEXT_EXTENSIONS and not any(part in SKIP_PARTS or part.startswith(".") for part in relative.parts)


def _clean_text(text: str) -> str:
    if "\x00" in text:
        return ""
    return text[:MAX_CHARS_PER_FILE]


def index_local_project(root: str | Path) -> list[dict]:
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"local project path is not a directory: {root}")
    docs = []
    for path in sorted(root.rglob("*")):
        if len(docs) >= MAX_FILES:
            break
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if not _allowed(rel) or path.stat().st_size > MAX_CHARS_PER_FILE * 4:
            continue
        try:
            content = _clean_text(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if content.strip():
            docs.append({"path": rel.as_posix(), "title": rel.name, "content": content,
                         "url": path.as_uri(), "source": "local"})
    return docs


def index_github_project(url: str, token: str | None = None, timeout: float = 20) -> list[dict]:
    parsed = parse_github_url(url)
    if not parsed:
        raise ValueError("a GitHub repository URL is required")
    owner, name = parsed
    headers = {"User-Agent": _UA}
    token = token or _github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    tree = requests.get(f"{GITHUB_API}/repos/{owner}/{name}/git/trees/HEAD?recursive=1", headers=headers, timeout=timeout)
    tree.raise_for_status()
    paths = [item["path"] for item in tree.json().get("tree", []) if item.get("type") == "blob"]
    docs = []
    for raw_path in paths:
        if len(docs) >= MAX_FILES:
            break
        rel = Path(raw_path)
        if not _allowed(rel):
            continue
        raw = requests.get(f"https://raw.githubusercontent.com/{owner}/{name}/HEAD/{quote(raw_path)}", headers=headers, timeout=timeout)
        if raw.status_code != 200:
            continue
        content = _clean_text(raw.text)
        if content.strip():
            docs.append({"path": raw_path, "title": rel.name, "content": content,
                         "url": f"https://github.com/{owner}/{name}/blob/HEAD/{quote(raw_path)}", "source": "github"})
    return docs


def index_project(project: dict, local_path: str | None = None) -> list[dict]:
    return index_local_project(local_path) if local_path else index_github_project(project.get("url") or "")
