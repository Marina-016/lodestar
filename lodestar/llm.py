"""LLM 客户端：live（Anthropic Messages API）+ mock（离线夹具）。

设计说明（对应 PRD 缺口 A1/A2）：
- 所有 LLM 调用走 `complete` / `complete_json`，按 role 标记，便于 mock 匹配与 Trace。
- live 模式缺 ANTHROPIC_API_KEY 时给出明确报错，绝不静默。
- mock 模式不烧 token、确定性输出，冒烟测试与回归用。
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict

try:
    import anthropic
except ImportError:  # 未装 SDK 时 live 模式会在使用时报错，mock 不受影响
    anthropic = None

SYSTEM_ROLE_MARKER = "# ROLE: {role}"


class LLMError(Exception):
    pass


def _extract_json(text: str) -> Any:
    """从 LLM 文本里稳健抽取第一个 JSON 对象/数组。"""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 平衡括号扫描
    for opener, closer in (("{", "}"), ("[", "]")):
        if opener not in text:
            continue
        start = text.index(opener)
        depth = 0
        for i in range(start, len(text)):
            ch = text[i]
            if ch == opener:
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except json.JSONDecodeError:
                        break
    raise LLMError(f"无法从 LLM 输出解析 JSON。输出前 300 字符：{text[:300]!r}")


class LLMClient:
    def __init__(self, config, judge: bool = False):
        self.config = config
        self.judge = judge
        self.mode = config.llm_mode
        self.model = config.judge_model if judge else config.model
        self._client = None
        if self.mode == "live":
            if anthropic is None:
                raise LLMError("anthropic SDK 未安装，无法使用 live 模式。")
            self._client = anthropic.Anthropic(timeout=config.llm_timeout_s)

    # ---------- 对外接口 ----------
    def complete(self, role: str, system: str, user: str, max_tokens: int | None = None) -> str:
        if self.mode == "mock":
            return MockLLM.complete(role, system, user)
        return self._complete_live(role, system, user, max_tokens)

    def complete_json(self, role: str, system: str, user: str, max_tokens: int | None = None) -> Dict:
        if self.mode == "mock":
            text = MockLLM.complete(role, system, user)
        else:
            text = self._complete_live(role, system, user, max_tokens)
        data = _extract_json(text)
        if not isinstance(data, dict):
            raise LLMError(f"期望 JSON 对象，实际得到 {type(data).__name__}：{str(data)[:200]}")
        return data

    # ---------- 内部 ----------
    def _complete_live(self, role: str, system: str, user: str, max_tokens: int | None) -> str:
        """默认关 thinking（省 token、防空输出）；空文本重试一次（预算×2）；
        thinking 参数不被模型支持时自动去掉重试。"""
        mt = max_tokens or (self.config.judge_max_tokens if self.judge else self.config.max_tokens)
        kw: dict = {}
        if not self.config.llm_thinking:
            kw["thinking"] = {"type": "disabled"}
        for attempt in (1, 2):
            try:
                resp = self._client.messages.create(
                    model=self.model, max_tokens=mt, temperature=self.config.temperature,
                    system=system, messages=[{"role": "user", "content": user}], **kw,
                )
                text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
                if text.strip():
                    return text
                if attempt == 1:  # 空文本：可能是思考块吃光预算 → 加大预算并关 thinking 重试
                    mt *= 2
                    kw.pop("thinking", None)
                    continue
                raise LLMError(f"LLM 返回空文本（model={self.model}）")
            except anthropic.AuthenticationError as e:  # type: ignore[union-attr]
                raise LLMError(f"LLM 鉴权失败，请检查 token/base_url：{e}") from e
            except anthropic.APIStatusError as e:  # type: ignore[union-attr]
                if attempt == 1 and kw.get("thinking"):  # thinking 参数不被支持 → 去掉重试
                    kw.pop("thinking", None)
                    continue
                raise LLMError(f"LLM API 错误（{e.status_code}）：{e}") from e
            except anthropic.APIError as e:  # type: ignore[union-attr]
                raise LLMError(f"LLM API 错误：{e}") from e
            except Exception as e:
                raise LLMError(f"LLM 调用失败（{self.model}）：{e}") from e


# =====================================================================
# Mock 夹具：确定性返回，匹配 system prompt 中的 "# ROLE: <role>"。
# 输出用研究意图的占位内容，仅验证管道正确性，不代表真实研究质量。
# =====================================================================
class MockLLM:
    @staticmethod
    def complete(role: str, system: str, user: str) -> str:
        fn = getattr(MockLLM, f"_role_{role}", None)
        if fn is None:
            raise LLMError(f"mock 模式未实现 role={role!r}，请检查 prompts 与 mock 同步。")
        return fn(system, user)

    # --- 主题感知：从目标推断主题，返回对应主题的 LLM 夹具 ---
    _TOPIC_LLM = {
        "self_evolving": {
            "questions": [
                "核心技术路径是什么（Experience→Reflection→Candidate→Eval→Promotion）",
                "与 Skill / Memory / Eval 是什么关系",
                "近期有哪些值得关注的工作",
            ],
            "strategy": [
                "arXiv: self-improving / self-evolving agents",
                "web: self-evolving skill, automatic skill discovery",
                "对比各工作改进的是哪一层（Prompt/Skill/Memory/Policy）",
            ],
            "queries": [
                {"text": "self-evolving agents self-improving agents arxiv", "purpose": "论文检索：agent 自改进"},
                {"text": "self-evolving skill automatic skill discovery", "purpose": "网页检索：skill 自动演进"},
                {"text": "agent self-improvement memory reflection evaluation", "purpose": "综合：与 Memory/Eval 的关系"},
            ],
            "synthesis": (
                "## 共同观点\n"
                "Self-Evolving Agent 的核心是把运行反馈转化为结构化改进信号，再经评估把关后推广（Promotion）。\n\n"
                "## 主要技术路线\n"
                "1. 经验收集（Experience/Trace Collection）→ 失败或反馈（Failure/Feedback）→ 反思（Reflection）"
                " → 候选改进（Candidate）→ 评估（Evaluation）→ 推广（Promotion）。\n\n"
                "## 方法差异\n"
                "不同工作改进的层次不同：Prompt / Skill / Memory / Policy / Tool 策略。\n\n"
                "## 相互冲突\n"
                "自改进能否收敛、评估信号噪声两大争议点（mock 占位，需真实阅读补充）。\n\n"
                "## 研究空白\n"
                "缺乏统一的评估基准，跨工作难以直接比较。\n\n"
                "## 当前趋势\n"
                "从 Prompt 级自优化走向 Skill/Memory 级结构化自演进（self-improving agents 是当前主流表述），"
                "并与 Eval/Regression 流程结合。"
            ),
            "claims": [
                {
                    "claim": "Self-Evolving Agent 通过 Evaluation-driven Promotion 把候选改进推广为正式能力",
                    "concept": "Self-Evolving Agent", "novelty": "high", "is_repackaging_of": None,
                    "reason": "相对已知的 Skill+Eval 组合，把 Eval 作为 Promotion 关卡是结构化增量（mock 判定）",
                },
                {
                    "claim": "Evolving 的载体可以是 Skill / Memory / Policy 等不同层",
                    "concept": "Self-Evolving Skill", "novelty": "medium", "is_repackaging_of": "Skill",
                    "reason": "Skill 可演进是已有认知，区分各层改进是新增视角（mock 判定）",
                },
            ],
        },
        "agent_memory": {
            "questions": [
                "Agent Memory 的主要技术路径（存储/检索/更新）是什么",
                "分层记忆（episodic/semantic/procedural）如何组织",
                "与 Context Engineering、Eval 的关系",
            ],
            "strategy": [
                "arXiv: agent memory taxonomy, memory updating, retrieval",
                "web: memory vs context engineering",
            ],
            "queries": [
                {"text": "agent memory episodic semantic procedural taxonomy arxiv", "purpose": "分层记忆分类"},
                {"text": "agent memory updating retrieval context window", "purpose": "更新与检索路径"},
                {"text": "agent memory evaluation self-improvement", "purpose": "与 Eval/自改进关系"},
            ],
            "synthesis": (
                "## 共同观点\n"
                "Agent Memory 正从「向量库即记忆」走向分层记忆：episodic（情景）、semantic（语义）、"
                "procedural（程序）三类记忆各有侧重。\n\n"
                "## 主要技术路线\n"
                "1. 存储：向量数据库与结构化记忆；2. 检索：relevance 检索；"
                "3. 更新：memory updating（改写/合并）而非仅 append-only；"
                "4. 自改进：用 evaluation 反馈驱动记忆重写与 skill 沉淀。\n\n"
                "## 方法差异\n"
                "差异在检索粒度、更新策略（append vs revise）、以及与 context window 的衔接。\n\n"
                "## 相互冲突\n"
                "memory updating 是否安全（遗忘 vs 一致性）是主要分歧。\n\n"
                "## 研究空白\n"
                "缺乏跨 memory 架构的统一 benchmark 与 evaluation 协议。\n\n"
                "## 当前趋势\n"
                "记忆层与 context engineering 融合，检索不再是唯一入口。"
            ),
            "claims": [
                {
                    "claim": "Agent Memory 分层化（episodic/semantic/procedural）与 evaluation 反馈驱动的 memory updating 是新增量",
                    "concept": "Agent Memory", "novelty": "high", "is_repackaging_of": None,
                    "reason": "把 Memory 从纯检索存储扩展为可更新的记忆层是结构化增量（mock 判定）",
                },
                {
                    "claim": "记忆与 context window 的衔接需要显式设计",
                    "concept": "Memory Updating", "novelty": "medium", "is_repackaging_of": "Memory",
                    "reason": "Memory 已知，更新机制是延伸视角（mock 判定）",
                },
            ],
        },
        "context_engineering": {
            "questions": [
                "Context Engineering 的主要技术路径（压缩/检索/预算）",
                "context window 管理的最佳实践",
                "与 Memory、Harness 的职责划分",
            ],
            "strategy": [
                "arXiv: context engineering, context compression, retrieval augmentation",
                "web: context window budget best practices",
            ],
            "queries": [
                {"text": "context engineering compression retrieval window arxiv", "purpose": "技术路径"},
                {"text": "context window budget prompt structure", "purpose": "预算与 prompt 结构"},
                {"text": "context engineering memory harness", "purpose": "与 Memory/Harness 关系"},
            ],
            "synthesis": (
                "## 共同观点\n"
                "Context Engineering 把上下文当作一等公民：在有限的 context window 里决定放什么、不放什么。\n\n"
                "## 主要技术路线\n"
                "1. 压缩：summarization 与 token 级 compression；2. 检索增强：把 memory/文档检索进上下文；"
                "3. 预算管理：prompt 结构与 window 分配；4. 与 harness 联动：运行时上下文分层。\n\n"
                "## 方法差异\n"
                "差异在压缩粒度、检索触发时机、以及是否重写历史而非只追加。\n\n"
                "## 相互冲突\n"
                "压缩的保真度 vs 成本；检索的召回 vs 噪声。\n\n"
                "## 研究空白\n"
                "缺少跨方法统一评价指标。\n\n"
                "## 当前趋势\n"
                "Context Engineering 与 Memory、Harness 分层职责逐渐清晰。"
            ),
            "claims": [
                {
                    "claim": "把上下文作为一等公民、按预算/压缩/检索分层管理是新增量",
                    "concept": "Context Engineering", "novelty": "high", "is_repackaging_of": None,
                    "reason": "从 Prompt 工程上升为 context 全生命周期管理是结构化增量（mock 判定）",
                },
                {
                    "claim": "压缩与检索增强的取舍",
                    "concept": "Context Compression", "novelty": "medium", "is_repackaging_of": "Prompt Optimization",
                    "reason": "压缩是 Prompt 优化的延伸（mock 判定）",
                },
            ],
        },
        "agent_eval": {
            "questions": [
                "Agent Eval 从 final-answer 到 trajectory 级的转变",
                "LLM-as-Judge 的可靠性与校准",
                "Eval/Regression 在 agent 开发循环中的位置",
            ],
            "strategy": [
                "arXiv: agent evaluation benchmark trajectory",
                "web: llm-as-judge reliability, regression-driven development",
            ],
            "queries": [
                {"text": "agent evaluation trajectory benchmark arxiv", "purpose": "评测方法"},
                {"text": "llm as judge calibration reliability", "purpose": "Judge 可靠性"},
                {"text": "agent eval regression trace development loop", "purpose": "开发循环集成"},
            ],
            "synthesis": (
                "## 共同观点\n"
                "Agent Eval 的重心从「最终答案」转向 trajectory 级评测：过程与结果同样重要。\n\n"
                "## 主要技术路线\n"
                "1. benchmark：专门 agent 任务集；2. LLM-as-Judge：可解释、可校准但需防偏；"
                "3. trajectory 评测：结合 trace 检查工具使用与中间步骤；4. regression：把 eval 接入 agent 开发循环防退化。\n\n"
                "## 方法差异\n"
                "分歧在 judge 的可靠性（self-bias、长度偏好）、评测任务是否真实反映 agent 能力。\n\n"
                "## 相互冲突\n"
                "人工评测昂贵但可信 vs LLM judge 便宜但有偏。\n\n"
                "## 研究空白\n"
                "跨 benchmark 的通用评测协议缺失。\n\n"
                "## 当前趋势\n"
                "Eval 成为 agent 开发的一等环节，与 trace/regression 深度绑定。"
            ),
            "claims": [
                {
                    "claim": "从 final-answer 转向 trajectory 级评测（结合 trace）是新增量",
                    "concept": "Agent Eval", "novelty": "high", "is_repackaging_of": None,
                    "reason": "把评测单元从输出扩展到执行轨迹是结构化增量（mock 判定）",
                },
                {
                    "claim": "LLM-as-Judge 需要校准以控制偏差",
                    "concept": "LLM Judge Calibration", "novelty": "medium", "is_repackaging_of": "Agent Eval",
                    "reason": "Judge 可靠性是 Agent Eval 的延伸视角（mock 判定）",
                },
            ],
        },
        "mcp": {
            "questions": [
                "MCP（Model Context Protocol）的协议设计与 client-server 结构",
                "工具/资源/权限模型",
                "与 Agent Harness、Tool Calling 的关系",
            ],
            "strategy": [
                "arXiv: mcp protocol model context",
                "web: mcp servers clients ecosystem",
            ],
            "queries": [
                {"text": "MCP model context protocol tool server client arxiv", "purpose": "协议设计"},
                {"text": "mcp tool protocol permissions audit", "purpose": "权限与安全模型"},
                {"text": "mcp agent harness integration", "purpose": "与 harness 集成"},
            ],
            "synthesis": (
                "## 共同观点\n"
                "MCP（Model Context Protocol）统一了模型与工具/资源的连接协议，client-server 结构解耦。\n\n"
                "## 主要技术路线\n"
                "1. 协议：JSON-RPC 之上的工具/资源/提示定义；2. server：封装工具与数据；"
                "3. client：接入 agent/harness；4. 权限：工具调用的授权与审计。\n\n"
                "## 方法差异\n"
                "差异在 server 的粒度、认证方式、以及工具 schema 的规范化程度。\n\n"
                "## 相互冲突\n"
                "协议统一 vs 各家自定义；MCP 是否足够表达复杂工具依赖。\n\n"
                "## 研究空白\n"
                "跨 server 的可组合性与安全模型研究不足。\n\n"
                "## 当前趋势\n"
                "MCP 成为 agent harness 连接外部能力的标准接口。"
            ),
            "claims": [
                {
                    "claim": "MCP 用标准 client-server 协议统一工具/资源/权限模型是新增量",
                    "concept": "MCP", "novelty": "high", "is_repackaging_of": None,
                    "reason": "把工具连接协议化为可组合标准是结构化增量（mock 判定）",
                },
                {
                    "claim": "MCP 是 Tool Calling 的协议化延伸",
                    "concept": "Tool Protocol", "novelty": "medium", "is_repackaging_of": "Tool Calling",
                    "reason": "Tool Calling 已知，协议化是延伸视角（mock 判定）",
                },
            ],
        },
    }

    # --- planner ---
    @staticmethod
    def _extract_goal(user: str) -> str:
        m = re.search(r"## 研究目标\s*\n(.+)", user)
        return m.group(1).strip() if m else (user.strip().splitlines()[0] if user.strip() else "")

    @staticmethod
    def _topic(user: str) -> str:
        from lodestar.fixtures import topic_from_text
        return topic_from_text(MockLLM._extract_goal(user))

    @staticmethod
    def _role_planner(system: str, user: str) -> str:
        goal = MockLLM._extract_goal(user)
        t = MockLLM._TOPIC_LLM[MockLLM._topic(user)]
        return json.dumps(
            {
                "goal": goal,
                "research_questions": t["questions"],
                "search_strategy": t["strategy"],
                "expected_output": ["核心路径综述", "Top 来源列表", "Novelty 判定", "与已有知识的关系"],
            },
            ensure_ascii=False,
        )

    # --- query expansion ---
    @staticmethod
    def _role_queries(system: str, user: str) -> str:
        t = MockLLM._TOPIC_LLM[MockLLM._topic(user)]
        return json.dumps({"original": MockLLM._extract_goal(user), "queries": t["queries"]}, ensure_ascii=False)

    # --- rerank：解析 user 里编号的来源行，原序返回 Top N ---
    @staticmethod
    def _role_rerank(system: str, user: str) -> str:
        indexes = [int(m) for m in re.findall(r"^\s*(\d+)[.)]\s+", user, flags=re.M)]
        ranked = [{"index": i, "score": 10 - idx, "reason": "mock rerank（离线夹具）"} for idx, i in enumerate(indexes[:8])]
        return json.dumps({"ranked": ranked}, ensure_ascii=False)

    # --- assess ---
    @staticmethod
    def _role_assess(system: str, user: str) -> str:
        return json.dumps(
            {"sufficient": True, "gaps": [], "decision": "synthesize", "reason": "mock assess（离线夹具）"},
            ensure_ascii=False,
        )

    # --- synthesis（markdown 文本，非 JSON）---
    @staticmethod
    def _role_synthesis(system: str, user: str) -> str:
        return MockLLM._TOPIC_LLM[MockLLM._topic(user)]["synthesis"]

    # --- novelty ---
    @staticmethod
    def _role_novelty(system: str, user: str) -> str:
        t = MockLLM._TOPIC_LLM[MockLLM._topic(user)]
        return json.dumps({"claims": t["claims"], "overall_novelty": "medium"}, ensure_ascii=False)

    # --- project_relevance：研究机会 × 用户项目 ---
    @staticmethod
    def _role_project_relevance(system: str, user: str) -> str:
        names = re.findall(r"^- ([A-Za-z0-9_.\-/]+)（", user, flags=re.M)
        first = names[0] if names else "（无项目）"
        return json.dumps({"mappings": [{"opportunity_index": 0,
                                         "applicable": [first],
                                         "reason": f"mock 判定：该方向与项目 {first} 的技术栈/领域相关。"}]},
                          ensure_ascii=False)

    # --- quiz：知识评估 ---
    @staticmethod
    def _role_quiz_question(system: str, user: str) -> str:
        m = re.search(r"## 概念\s*\n(.+)", user)
        concept = m.group(1).strip() if m else "该概念"
        return json.dumps({"question": f"请解释「{concept}」的核心原理：它解决什么问题、核心技术路径是什么，"
                                      "并给出一个实际应用或反例。"}, ensure_ascii=False)

    @staticmethod
    def _role_quiz_eval(system: str, user: str) -> str:
        return json.dumps({"status": "partial", "confidence": "medium",
                           "feedback": "mock 评估：回答有一定基础但细节不足，建议补充原理与边界。",
                           "next_question": None}, ensure_ascii=False)

    # --- frontier：Weekly AI Frontier Research 策展 ---
    @staticmethod
    def _role_frontier(system: str, user: str) -> str:
        return json.dumps(
            {"suggestions": [
                {"topic": "Agent Memory 分层与更新机制的最新进展", "why": "你的 Knowledge State 中 Memory 为 known/medium，"
                 "但 episodic/procedural 分层与 memory updating 的具体实现未覆盖，本周有多篇相关工作值得关注。", "priority": "high"},
                {"topic": "Context Engineering 与 Agent Harness 的融合边界", "why": "Context Engineering 为 known/high，"
                 "但 Harness 为 partial/low，本周关于两者融合的新框架值得研究。", "priority": "medium"},
                {"topic": "MCP 生态最近的实现与争议", "why": "MCP 已进入快速迭代期，Tool Protocol 与 Tool Calling 的关系"
                 "是当前热点，你的 Knowledge State 中 Protocol 为 partial/low。", "priority": "high"},
            ]}, ensure_ascii=False)

    # --- gap_queries：assess 缺口 → 检索 Query ---
    @staticmethod
    def _role_gap_queries(system: str, user: str) -> str:
        # 简单提取缺口中的关键词作为 mock query
        return "supplemental search mock gap query"
    @staticmethod
    def _role_judge_task_success(system: str, user: str) -> str:
        return json.dumps({"score": 4, "rationale": "mock judge：管道完整性通过（离线夹具，非内容质量判定）"}, ensure_ascii=False)

    @staticmethod
    def _role_judge_faithfulness(system: str, user: str) -> str:
        return json.dumps({"score": 4, "rationale": "mock judge（离线夹具）"}, ensure_ascii=False)

    @staticmethod
    def _role_judge_planning(system: str, user: str) -> str:
        return json.dumps({"score": 4, "rationale": "mock judge（离线夹具）"}, ensure_ascii=False)

    @staticmethod
    def _role_judge_novelty(system: str, user: str) -> str:
        return json.dumps({"score": 4, "rationale": "mock judge（离线夹具）"}, ensure_ascii=False)
