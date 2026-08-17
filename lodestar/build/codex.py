"""Codex CLI executor（headless `codex exec`）。

注意：本环境实测 codex 绑定 ChatGPT 云认证（chatgpt.com 不可达）而失败；
需在配置了 OpenAI 兼容端点（OPENAI_BASE_URL + OPENAI_API_KEY）或已登录的环境才可用。
"""
from __future__ import annotations

from lodestar.build.executor import BuildExecutor, ExecutorResult


class CodexExecutor(BuildExecutor):
    name = "codex"

    def __init__(self, model: str | None = None):
        self.model = model

    def _binary(self) -> str:
        return "codex"

    def run(self, prompt: str, cwd: str = ".", timeout: int = 300) -> ExecutorResult:
        cmd = ["codex", "exec", "--skip-git-repo-check", prompt]
        if self.model:
            cmd += ["--model", self.model]
        return self._exec(cmd, cwd, timeout)
