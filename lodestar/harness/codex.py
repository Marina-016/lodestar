"""Codex Harness adapter for the conversation-first UI.

This is opt-in. The normal UI continues to use ResearchAgent so existing
Trace/Eval behavior remains unchanged. When enabled, Codex receives the
Lodestar MCP server as a per-process tool and can choose search/read/memory
calls itself.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from lodestar.build.codex import CodexExecutor
from lodestar.config import Config, PROJECT_ROOT


class CodexConversationHarness:
    name = "codex"

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.executor = CodexExecutor(
            model=cfg.codex_model,
            provider=cfg.codex_provider_name if cfg.codex_base_url else None,
            base_url=cfg.codex_base_url or None,
            require_gateway=False,
        )

    def available(self) -> bool:
        return self.executor.available()

    @staticmethod
    def _mcp_overrides() -> list[str]:
        python = json.dumps(sys.executable)
        args = json.dumps(["-m", "lodestar", "mcp"])
        return [
            "-c", f"mcp_servers.lodestar.command={python}",
            "-c", f"mcp_servers.lodestar.args={args}",
        ]

    def run(self, task_id: str, goal: str):
        prompt = f"""
你是 Lodestar 的研究对话搭档。用户的问题是：
{goal}

请直接用中文回答用户，先判断需要什么证据，再自主选择 Lodestar MCP 工具完成搜索、阅读或知识库操作。
不要编造来源；如果证据不足，明确说出缺口并继续检索或向用户追问。
回答要适合对话阅读：先给结论，再给关键证据和下一步。只有用户明确要求“记住”时才写入知识库。
当前 Lodestar task_id：{task_id}
""".strip()
        command = [
            "codex", "exec", "--skip-git-repo-check", "--sandbox", "read-only",
            *self._mcp_overrides(), prompt,
        ]
        return self.executor._exec(
            command,
            cwd=str(PROJECT_ROOT),
            timeout=self.cfg.llm_timeout_s,
            env={"LODESTAR_MCP_TASK_ID": task_id},
        )


__all__ = ["CodexConversationHarness"]