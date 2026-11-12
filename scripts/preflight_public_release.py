"""Check the working tree before making the repository public.

This is intentionally dependency-free so it can run in CI, locally, or on
Windows without installing another tool. It scans tracked files plus untracked
source files, while excluding local runtime state and environment files.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_PARTS = {".git", ".venv", "venv", "__pycache__", "workspace", "experiments", "lodestar/data"}
SECRET_PATTERNS = [
    re.compile(r"(?:sk|rk)-[A-Za-z0-9_-]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9_]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"-----BEGIN (?:RSA|OPENSSH|EC) PRIVATE KEY-----"),
    re.compile(r"(?im)^(?!\s*#).*?(?:ANTHROPIC|OPENAI|LODESTAR_CODEX)_?(?:API_KEY|AUTH_TOKEN)\s*=\s*(?!$|#|<[^>]+>|\$\{)[^\s#]+"),
]


def tracked_files() -> set[Path]:
    result = subprocess.run(["git", "ls-files"], cwd=ROOT, check=True, capture_output=True, text=True)
    return {ROOT / line for line in result.stdout.splitlines() if line}


def candidate_files() -> list[Path]:
    paths = set(tracked_files())
    result = subprocess.run(["git", "ls-files", "--others", "--exclude-standard", "-z"],
                            cwd=ROOT, check=True, capture_output=True)
    for name in result.stdout.decode('utf-8').split('\0'):
        if not name:
            continue
        path = ROOT / name
        relative = path.relative_to(ROOT).as_posix()
        if any(relative == part or relative.startswith(part + "/") for part in EXCLUDED_PARTS):
            continue
        if path.name in {".env", ".env.local"} or path.suffix in {".db", ".sqlite", ".sqlite3", ".pyc"}:
            continue
        paths.add(path)
    return sorted(paths)


def main() -> int:
    findings: list[str] = []
    large: list[str] = []
    candidates = candidate_files()
    for path in candidates:
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if len(data) > 1_000_000:
            large.append(f"{path.relative_to(ROOT)} ({len(data):,} bytes)")
        text = data.decode("utf-8", errors="ignore")
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                findings.append(f"{path.relative_to(ROOT)} matches {pattern.pattern}")
    print(f"Scanned {len(candidates)} candidate files.")
    if large:
        print("Large files:")
        print("\n".join(f"- {item}" for item in large))
    if findings:
        print("Potential secrets:")
        print("\n".join(f"- {item}" for item in findings))
        return 1
    print("No secret-like values found in the release set.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
