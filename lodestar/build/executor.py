"""Build Executor 抽象（PRD V3：Research → Build 接入 Coding Agent）。

V0.1.6 种子：把 prompt 交给外部 coding agent CLI（Claude Code / Codex）执行并捕获输出。
统一接口 run(prompt, cwd, timeout) → ExecutorResult；get_executor 按名解析。
"""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass


@dataclass
class ExecutorResult:
    ok: bool
    output: str = ""
    error: str = ""


class BuildExecutor:
    name: str = "base"

    def available(self) -> bool:
        """CLI 是否已安装（未安装也算不可用，调用时给明确提示）。"""
        return shutil.which(self._binary()) is not None

    def _binary(self) -> str:
        raise NotImplementedError

    def run(self, prompt: str, cwd: str = ".", timeout: int = 300) -> ExecutorResult:
        raise NotImplementedError

    def _exec(self, cmd: list[str], cwd: str, timeout: int, env: dict | None = None) -> ExecutorResult:
        # Windows 上 npm 的 shim（如 codex）无扩展名，subprocess 解析不了 → 用 which 解析后的全路径
        lookup_path = (env or {}).get("PATH") or (env or {}).get("Path")
        bin_path = shutil.which(self._binary(), path=lookup_path) if lookup_path else shutil.which(self._binary())
        if not bin_path:
            return ExecutorResult(ok=False, error=f"找不到 CLI：{self._binary()}（未安装或不在 PATH）")
        cmd[0] = bin_path
        full_env = None
        if env:
            import os
            full_env = {**os.environ, **env}
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               cwd=cwd, timeout=timeout, encoding="utf-8", errors="replace",
                               env=full_env)
            out = (r.stdout or "").strip()
            err = (r.stderr or "").strip()
            return ExecutorResult(ok=r.returncode == 0, output=out, error=err)
        except subprocess.TimeoutExpired:
            return ExecutorResult(ok=False, error=f"执行超时（{timeout}s）")
        except FileNotFoundError:
            return ExecutorResult(ok=False, error=f"找不到 CLI：{self._binary()}")
        except Exception as e:  # noqa: BLE001
            return ExecutorResult(ok=False, error=f"执行失败：{e}")


def get_executor(name: str = "auto") -> BuildExecutor:
    """按名解析 executor；auto = 依次探测可用性（claude 优先）。"""
    from lodestar.build.claude_code import ClaudeCodeExecutor
    from lodestar.build.codex import CodexExecutor
    registry = {"claude": ClaudeCodeExecutor, "codex": CodexExecutor}
    if name in registry:
        return registry[name]()
    if name != "auto":
        raise ValueError(f"未知 executor={name!r}（可选 claude/codex/auto）")
    for cls in (ClaudeCodeExecutor, CodexExecutor):
        inst = cls()
        if inst.available():
            return inst
    raise RuntimeError("无可用 coding agent CLI（claude 或 codex 均未安装）")
