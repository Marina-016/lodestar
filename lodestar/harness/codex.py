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
            proxy_url=cfg.codex_proxy_url or None,
            node_bin=cfg.codex_node_bin or None,
            require_gateway=False,
        )

    def available(self) -> bool:
        return self.executor.available()

    @staticmethod
    def _mcp_overrides(task_id: str) -> list[str]:
        python = json.dumps(sys.executable)
        args = json.dumps(["-m", "lodestar", "mcp"])
        task = json.dumps(task_id)
        return [
            "-c", f"mcp_servers.lodestar.command={python}",
            "-c", f"mcp_servers.lodestar.args={args}",
            "-c", f"mcp_servers.lodestar.env={{LODESTAR_MCP_TASK_ID={task}}}",
        ]

    def run(self, task_id: str, goal: str):
        compact_goal = " ".join(goal.split())
        prompt = (
            "You are Lodestar's research conversation partner. "
            f"User request: {compact_goal} "
            "Answer in Chinese. First determine the needed evidence, then autonomously use Lodestar MCP tools for search, reading, or knowledge operations. "
            "For questions about a registered project implementation, first use search_project_context and then read_project_file before making a code-grounded claim. "
            "Do not fabricate sources. If a tool returns an error, do not retry the same external request; use available evidence and state the gap. "
            "Only write to the knowledge base when the user explicitly asks you to remember something. "
            f"Current Lodestar task_id: {task_id}"
        )
        approval_args = (["--approve-for-me"] if self.cfg.codex_auto_approve
                         else ["--sandbox", "read-only"])
        command = [
            "codex", "exec", "--model", self.cfg.codex_model,
            "--skip-git-repo-check", *approval_args,
            *self._mcp_overrides(task_id), prompt,
        ]
        return self.executor._exec(
            command,
            cwd=str(PROJECT_ROOT),
            timeout=self.cfg.conversation_timeout_s,
            env={**self.executor._runtime_env(), "LODESTAR_MCP_TASK_ID": task_id},
        )


__all__ = ["CodexConversationHarness"]
