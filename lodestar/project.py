"""项目摄入：读取 GitHub 链接 → 项目元信息 + 技术栈。

- 用 GitHub API（metadata: language/topics/description）+ raw README。
- 私有仓库：token 来自 GITHUB_TOKEN 环境变量，或 git 凭据管理器（尽力而为）。
- 失败返回明确错误（不静默）；网络抖动由调用方兜底。
"""
from __future__ import annotations

import os
import re
import subprocess

import requests

GITHUB_API = "https://api.github.com"
_UA = "Lodestar/0.1 (project ingestion)"

_TECH_KEYWORDS = [
    "python", "typescript", "javascript", "react", "next.js", "vue", "fastapi", "flask",
    "django", "rust", "go", "java", "c++", "sqlite", "postgres", "mysql", "redis", "docker",
    "kubernetes", "langchain", "anthropic", "openai", "claude", "llm", "agent", "tensorflow",
    "pytorch", "node.js", "tailwind", "graphql", "grpc", "websocket", "pandas", "numpy",
    "aws", "gcp", "azure", "linux", "macos", "windows", "nextjs", "llamaindex", "transformers",
]


def parse_github_url(url: str) -> tuple[str, str] | None:
    m = re.search(r"github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", url or "")
    return (m.group(1), m.group(2)) if m else None


def _github_token() -> str | None:
    if os.environ.get("GITHUB_TOKEN"):
        return os.environ["GITHUB_TOKEN"]
    try:  # 尽力从 git 凭据管理器取（用于私有仓库）
        out = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n",
                             capture_output=True, text=True, timeout=10).stdout
        for line in out.splitlines():
            if line.startswith("password="):
                return line.split("=", 1)[1]
    except Exception:  # noqa: BLE001
        pass
    return None


def fetch_github_metadata(owner: str, repo: str, token: str | None = None, timeout: float = 20) -> dict:
    headers = {"User-Agent": _UA}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    resp = requests.get(f"{GITHUB_API}/repos/{owner}/{repo}", headers=headers, timeout=timeout)
    resp.raise_for_status()
    d = resp.json()
    return {
        "name": d.get("full_name") or f"{owner}/{repo}",
        "url": d.get("html_url") or f"https://github.com/{owner}/{repo}",
        "description": d.get("description") or "",
        "language": d.get("language") or "",
        "topics": d.get("topics") or [],
        "pushed_at": (d.get("pushed_at") or "")[:10],
    }


def fetch_github_readme(owner: str, repo: str, token: str | None = None, timeout: float = 20) -> str:
    headers = {"User-Agent": _UA}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    resp = requests.get(f"https://raw.githubusercontent.com/{owner}/{repo}/HEAD/README.md",
                        headers=headers, timeout=timeout)
    if resp.status_code != 200:
        return ""
    return resp.text[:20000]


def infer_tech_stack(language: str, readme: str, topics: list[str] | None = None) -> list[str]:
    text = f"{language} {' '.join(topics or [])} {readme}".lower()
    found = []
    for kw in _TECH_KEYWORDS:
        if kw in text:
            found.append(kw)
    return sorted(set(found))[:12]


def ingest_github(url: str, token: str | None = None, timeout: float = 20) -> dict:
    """摄入一个 GitHub 仓库链接 → 项目字典（可直接 upsert_project）。失败抛异常。"""
    parsed = parse_github_url(url)
    if not parsed:
        raise ValueError(f"不是有效的 GitHub 链接: {url}")
    owner, repo = parsed
    token = token or _github_token()
    meta = fetch_github_metadata(owner, repo, token, timeout)
    try:  # README 是可选信息：读不到不影响登记（元信息已够判断技术栈/领域）
        readme = fetch_github_readme(owner, repo, token, timeout)
    except Exception:  # noqa: BLE001
        readme = ""
    stack = infer_tech_stack(meta.get("language", ""), readme, meta.get("topics"))
    desc = meta.get("description") or (readme.split("\n", 1)[0][:150] if readme else "")
    return {
        "name": meta["name"],
        "url": meta["url"],
        "description": desc,
        "tech_stack": stack,
        "last_push": meta.get("pushed_at", ""),
    }
