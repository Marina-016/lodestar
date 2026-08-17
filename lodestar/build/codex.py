"""Codex CLI executor（headless `codex exec`）——Lodestar Build 步的主 executor。

Codex CLI 是 **Apache-2.0 开源**（github.com/openai/codex），可 fork/内嵌/随项目分发。
两种运行模式（实测均可用）：
- **默认（用户自带 codex 配置）**：`provider=None`，直接用本机 codex 配置（如 ChatGPT 登录）。
- **内网网关模式**：`provider/base_url/api_key` 给定后，注入 codex 运行时配置
  （`-c model_providers.<name>...`），走 OpenAI Responses API 兼容网关。
  实测 codex 0.139 需 `wire_api="responses"`（chat 已弃用），网关 sh-dtrouter 支持该格式。
  注意：chatgpt.com 云同步的 MCP worker 报错是无害噪音，不影响执行。
"""
from __future__ import annotations

import os

from lodestar.build.executor import BuildExecutor, ExecutorResult


class CodexExecutor(BuildExecutor):
    name = "codex"

    def __init__(self, model: str | None = None, provider: str | None = None,
                 base_url: str | None = None, api_key: str | None = None):
        self.model = model
        self.provider = provider          # 自定义 provider 名（如 lodestar-gw）
        self.base_url = base_url          # 自定义端点（如 http://<gw>/v1）
        self.api_key = api_key            # 注入为 OPENAI_API_KEY 传给子进程

    def _binary(self) -> str:
        return "codex"

    def run(self, prompt: str, cwd: str = ".", timeout: int = 300) -> ExecutorResult:
        cmd = ["codex", "exec", "--skip-git-repo-check"]
        env = None
        if self.provider and self.base_url:
            name = self.provider
            cmd += [
                "-c", f'model="{self.model or "deepseek-v4-flash"}"',
                "-c", f'model_provider="{name}"',
                "-c", f'model_providers.{name}.name="{name}"',
                "-c", f'model_providers.{name}.base_url="{self.base_url}"',
                '-c', f'model_providers.{name}.wire_api="responses"',
                '-c', f'model_providers.{name}.env_key="OPENAI_API_KEY"',
            ]
            key = self.api_key or os.environ.get("LODESTAR_CODEX_API_KEY") or os.environ.get("OPENAI_API_KEY")
            if key:
                env = {"OPENAI_API_KEY": key}
            elif self.provider:
                return ExecutorResult(ok=False, error="codex 网关模式需要 LODESTAR_CODEX_API_KEY / OPENAI_API_KEY")
        cmd.append(prompt)
        return self._exec(cmd, cwd, timeout, env=env)
