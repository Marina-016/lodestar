"""Codex CLI executor（headless `codex exec`）。

Codex CLI 是 **Apache-2.0 开源**（github.com/openai/codex）——可 fork/内嵌/随项目分发，
适合作为 Lodestar 开源化时的 Build executor。本环境实测 codex 绑定 ChatGPT 云认证
（chatgpt.com 不可达）而失败；配置 OpenAI 兼容端点（OPENAI_BASE_URL + OPENAI_API_KEY）
或完成登录后，`python -m lodestar build "<prompt>" --executor codex` 即可用。
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
