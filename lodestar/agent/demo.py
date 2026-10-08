"""Isolated backend demonstrations: deterministic fixtures or explicit recordings.

This module never uses the user's environment configuration or live clients.
"""
from __future__ import annotations

import json
from pathlib import Path

from lodestar.agent.conversation import ConversationAgent
from lodestar.config import Config
from lodestar.context import Workspace
from lodestar.llm import LLMClient
from lodestar.memory import learning


def _write(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def run_demo(output: Path, recording: Path | None = None) -> dict:
    """Create a new directory; never reset an existing workspace or call a model."""
    output = output.resolve()
    saved = None
    if recording is not None:
        saved = {name: json.loads((recording / f"{name}.json").read_text(encoding="utf-8"))
                 for name in ("research", "followup")}
        for name in ('learning', 'feedback', 'plan', 'plan-review', 'provenance'):
            path = recording / f'{name}.json'
            if path.is_file():
                saved[name] = json.loads(path.read_text(encoding='utf-8'))
    output.mkdir(parents=True, exist_ok=False)
    if saved is not None:
        for name, value in saved.items():
            _write(output / f"{name}.json", value)
        manifest = {"mode": "recorded_replay", "live": False, "model_calls": 0,
                    "recording": str(recording.resolve()),
                    "warning": "Historical model output; replay does not prove current discovery or answer accuracy.",
                    "project_plan": "recorded_draft" if "plan" in saved else "not_included"}
    else:
        cfg = Config(llm_mode="mock", search_mode="mock", model_calls_disabled=True,
                     enrich_venues=False, db_path=output / "demo.db",
                     workspace_dir=output / "tasks", pdf_cache_dir=output / "pdfs")
        ws = Workspace(cfg)
        try:
            agent = ConversationAgent(ws, LLMClient(cfg))
            session = agent.start("demo-user")
            research = agent.turn(session, "agent memory research", user_id="demo-user", intent="research")
            followup = agent.turn(session, "Explain the method", user_id="demo-user", intent="followup")
            feedback = agent.turn(session, "I understand the outline", user_id="demo-user", intent="feedback",
                                  technology="Harness", method="example-method", feedback="self_report")
            profile = learning.profile(ws.conn, "demo-user")
            for name, value in {"research": research, "followup": followup,
                                "feedback": feedback, "learning": profile,
                                "history": agent.history(session, "demo-user")}.items():
                _write(output / f"{name}.json", value)
            if not profile or any(item["mastery"] != "unknown" for item in profile):
                raise RuntimeError("Demo acknowledgement must not establish mastery")
            manifest = {"mode": "offline_fixture", "live": False, "model_calls": 0,
                        "session": session, "evidence_reused": followup.get("evidence_reused", 0),
                        "mastery": "unknown", "project_plan": "not_verified",
                        "warning": "Fixtures validate state transitions, not live research or model quality."}
        finally:
            ws.close()
    manifest["output"] = str(output)
    _write(output / "manifest.json", manifest)
    return manifest
