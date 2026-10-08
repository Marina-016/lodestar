"""Cross-source Synthesis（PRD §13）：跨来源综合分析，禁止「Paper A 讲什么 / B 讲什么」式罗列。"""
from __future__ import annotations

from lodestar import prompts


def synthesize(cfg, llm, goal: str, questions: list[str], read_sources: list[dict],
               knowledge_ctx: list[dict], learning_ctx: list[dict] | None = None) -> str:
    system, user = prompts.synthesis_prompt(cfg, goal, questions, read_sources, knowledge_ctx)
    from lodestar.memory.learning import prompt_context
    system += "\nAdapt explanations to the learning evidence. Research knowledge is not user mastery."
    user += "\n" + prompt_context(learning_ctx or [])
    # Let the orchestrator mark the task failed; a placeholder is not an answer.
    text = llm.complete("synthesis", system, user)
    from lodestar.agent.scope import annotate_scope
    return annotate_scope(text.strip())
