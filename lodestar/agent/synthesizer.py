"""Cross-source Synthesis（PRD §13）：跨来源综合分析，禁止「Paper A 讲什么 / B 讲什么」式罗列。"""
from __future__ import annotations

from lodestar import prompts


def synthesize(cfg, llm, goal: str, questions: list[str], read_sources: list[dict],
               knowledge_ctx: list[dict]) -> str:
    system, user = prompts.synthesis_prompt(cfg, goal, questions, read_sources, knowledge_ctx)
    try:
        text = llm.complete("synthesis", system, user)
    except Exception as e:  # noqa: BLE001 —— 综合失败也要能收尾并留痕
        return f"## 综合分析（生成失败）\nLLM 综合步骤报错：{e}\n已读取来源标题见 Trace。"
    return text.strip()
