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
import shutil

from lodestar.build.executor import BuildExecutor, ExecutorResult


class CodexExecutor(BuildExecutor):
    name = "codex"

    GATEWAY_REQUIRED_MSG = (
        "codex 需要网关模式：LODESTAR_CODEX_BASE_URL 未配置。"
        "为避免误用 ChatGPT Plus 额度（codex 默认会走 gpt-5.6-terra），Lodestar 默认拒绝以 codex 默认配置运行。"
        "任选其一：1) 配 LODESTAR_CODEX_BASE_URL=<网关>/v1 + LODESTAR_CODEX_API_KEY（走便宜的 deepseek-v4-flash）；"
        "2) 改用 --executor claude；3) 确认要用 ChatGPT 额度则设 LODESTAR_CODEX_REQUIRE_GATEWAY=false。"
    )

    def __init__(self, model: str | None = None, provider: str | None = None,
                 base_url: str | None = None, api_key: str | None = None,
                 require_gateway: bool = False, sandbox: str = "workspace-write",
                 proxy_url: str | None = None, node_bin: str | None = None):
        self.model = model
        self.provider = provider          # 自定义 provider 名（如 lodestar-gw）
        self.base_url = base_url          # 自定义端点（如 http://<gw>/v1）
        self.api_key = api_key            # 注入为 OPENAI_API_KEY 传给子进程
        self.require_gateway = require_gateway
        self.sandbox = sandbox            # read-only | workspace-write | danger-full-access
        self.proxy_url = proxy_url or ""
        self.node_bin = node_bin or ""

    def available(self) -> bool:
        lookup_path = self._runtime_env().get("PATH") or self._runtime_env().get("Path")
        return shutil.which(self._binary(), path=lookup_path) is not None

    def _binary(self) -> str:
        return "codex"

    def _runtime_env(self) -> dict[str, str]:
        """Scope local runtime requirements to Codex, not the parent process."""
        env: dict[str, str] = {}
        if self.node_bin:
            node_path = self.node_bin + os.pathsep + os.environ.get("PATH", os.environ.get("Path", ""))
            # Windows environment variable names are case-insensitive, but child-process merging is not.
            env["PATH"] = node_path
            env["Path"] = node_path
        if self.proxy_url:
            for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
                env[key] = self.proxy_url
            env["NO_PROXY"] = "127.0.0.1,localhost"
        return env

    def run(self, prompt: str, cwd: str = ".", timeout: int = 300) -> ExecutorResult:
        base = ["codex", "exec", "--skip-git-repo-check", "--sandbox", self.sandbox]
        if not (self.provider and self.base_url):
            if self.require_gateway:
                return ExecutorResult(ok=False, error=self.GATEWAY_REQUIRED_MSG)
            # 允许默认模式（用户显式关掉 require_gateway 后，走本机 codex 配置 / ChatGPT 登录）
            model_args = ["--model", self.model] if self.model else []
            return self._exec([*base, *model_args, prompt], cwd, timeout, env=self._runtime_env())
        cmd = list(base)
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
        if not key:
            return ExecutorResult(ok=False, error="codex 网关模式需要 LODESTAR_CODEX_API_KEY / OPENAI_API_KEY")
        cmd.append(prompt)
        return self._exec(cmd, cwd, timeout, env={**self._runtime_env(), "OPENAI_API_KEY": key})
