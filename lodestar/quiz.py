"""知识评估（Quiz）：agent 出题 → 用户回答 → 评估掌握程度 → 回写 Knowledge State。

填补 PRD 缺口 B1：除了用户自述 seed，还提供 agent 主动校准。
LLM 失败/超时不静默——返回 error 让前端明确提示（评估不能造假）。
"""
from __future__ import annotations

from lodestar.llm import LLMClient


def generate_question(cfg, llm: LLMClient, concept: str, user_knowledge: str) -> str:
    system = (
        "# ROLE: quiz_question\n\n"
        "你是 Lodestar（导星）的知识评估员。针对给定概念，设计一个能有效测试用户掌握程度的问题。\n"
        "要求：\n"
        "- 问题要能区分『真正理解』与『只知道名词』：请用户解释核心原理 / 说出技术路径 / 举应用例子，"
        "而不是简单是非题。\n"
        "- 难度略高于用户当前掌握程度（partial 就问原理，known 就问权衡与边界）。\n"
        '只输出 JSON：{"question": "问题"}\n'
        "问题用中文，技术术语保留英文。"
    )
    user = f"## 概念\n{concept}\n\n## 用户当前知识状态\n{user_knowledge}\n\n请设计评估问题。"
    data = llm.complete_json("quiz_question", system, user)
    return str(data.get("question") or "").strip()


def evaluate_answer(cfg, llm: LLMClient, concept: str, question: str, answer: str,
                    user_knowledge: str) -> dict:
    system = (
        "# ROLE: quiz_eval\n\n"
        "你是 Lodestar（导星）的知识评估员。根据用户对评估问题的回答，判断其对概念的掌握程度。\n"
        "只输出 JSON：\n"
        '{"status": "known|partial|unknown", "confidence": "low|medium|high", '
        '"feedback": "一句话反馈（指出对/错与差距）", '
        '"next_question": "如需追问确认则给一个更深入的问题，否则 null"}\n'
        "判定规则：能准确解释核心原理并联系实际 = known；部分正确或只知皮毛 = partial；"
        "明显错误/答非所问/空白 = unknown。feedback 要具体，不要空泛。"
    )
    user = f"## 概念\n{concept}\n\n## 问题\n{question}\n\n## 用户回答\n{answer}\n\n请评估。"
    data = llm.complete_json("quiz_eval", system, user)
    return {
        "status": data.get("status") if data.get("status") in {"known", "partial", "unknown"} else "partial",
        "confidence": data.get("confidence") if data.get("confidence") in {"low", "medium", "high"} else "medium",
        "feedback": str(data.get("feedback") or ""),
        "next_question": data.get("next_question"),
    }
