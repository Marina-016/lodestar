"""Claude Code CLI executor（headless `claude -p`）。

本环境实测可用（走 ANTHROPIC_BASE_URL/ANTHROPIC_AUTH_TOKEN 网关）。
参数化：--model / --output-format / --permission-mode 均可透传配置。
"""
from __future__ import annotations

from lodestar.build.executor import BuildExecutor, ExecutorResult


class ClaudeCodeExecutor(BuildExecutor):
    name = "claude"

    def __init__(self, model: str | None = None, permission_mode: str | None = None,
                 output_format: str = "text"):
        self.model = model
        self.permission_mode = permission_mode  # None | acceptEdits | bypassPermissions...
        self.output_format = output_format

    def _binary(self) -> str:
        return "claude"

    def run(self, prompt: str, cwd: str = ".", timeout: int = 300) -> ExecutorResult:
        cmd = ["claude", "-p", prompt, "--output-format", self.output_format]
        if self.model:
            cmd += ["--model", self.model]
        if self.permission_mode:
            cmd += ["--permission-mode", self.permission_mode]
        return self._exec(cmd, cwd, timeout)
