"""离线检索夹具（search_mode=mock）：让冒烟测试/回归不依赖网络，输出确定性。

按主题（topic）路由：每个主题一组论文+网页来源，供 MockLLM 与 mock 检索工具对齐使用，
使离线 eval 的覆盖率断言有真实意义。内容为占位，仅验证管道正确性，不代表真实研究质量。
"""
from __future__ import annotations

# ----------------------------------------------------------------------
# 主题路由：从文本（goal / query）推断主题 slug
# ----------------------------------------------------------------------
TOPIC_SLUGS = ("self_evolving", "agent_memory", "context_engineering", "agent_eval", "mcp")


# (短语, 主题) —— 取「最先出现的最具体短语」路由（主主题通常在句首；goal/query 常交叉提及相关主题）
_TOPIC_PHRASES = [
    ("model context protocol", "mcp"),
    ("context engineering", "context_engineering"),
    ("agent memory", "agent_memory"),
    ("self-evolv", "self_evolving"),
    ("self-improv", "self_evolving"),
    ("skill discover", "self_evolving"),
    ("skill learn", "self_evolving"),
    ("memory", "agent_memory"),
    ("context", "context_engineering"),
    ("benchmark", "agent_eval"),
    ("judge", "agent_eval"),
    ("eval", "agent_eval"),
    ("mcp", "mcp"),
]


def topic_from_text(text: str) -> str:
    t = (text or "").lower()
    best = None  # (位置, -短语长度, 主题)：位置最前优先，同位取更长更具体
    for phrase, topic in _TOPIC_PHRASES:
        pos = t.find(phrase)
        if pos >= 0:
            key = (pos, -len(phrase))
            if best is None or key < best[0]:
                best = (key, topic)
    return best[1] if best else "self_evolving"


# ----------------------------------------------------------------------
# 夹具构建器
# ----------------------------------------------------------------------
def _paper(tid: str, title: str, date: str, venue: str, published: bool, snippet: str) -> dict:
    return {
        "source_type": "paper",
        "title": title,
        "url": f"https://arxiv.org/abs/{tid}",
        "authors": ["Mock Author"],
        "date": date,
        "snippet": snippet,
        "dedup_key": f"arxiv:{tid}",
        "venue": venue,
        "is_published": published,
        "external_ids": {"ArXiv": tid},
    }


def _web(tid: str, title: str, snippet: str) -> dict:
    return {
        "source_type": "web",
        "title": title,
        "url": f"https://example.com/{tid}",
        "authors": [],
        "date": "",
        "snippet": snippet,
        "dedup_key": f"title:{tid}",
    }


# ----------------------------------------------------------------------
# 各主题来源
# ----------------------------------------------------------------------
TOPIC_SOURCES = {
    "self_evolving": {
        "papers": [
            _paper("2404.00001",
                   "Self-Evolving Agents: Experience-Driven Skill Learning with Evaluation-Driven Promotion",
                   "2025-11-02", "arXiv preprint", False,
                   "We propose an experience-driven framework where agents reflect on execution traces, "
                   "propose skill candidates, and promote them only after passing evaluation and regression."),
            _paper("2404.00002",
                   "Automatic Skill Discovery via Reflection over Memory: A Survey",
                   "2026-01-15", "NeurIPS 2025", True,
                   "A survey of automatic skill discovery methods, contrasting prompt-level optimization, "
                   "memory-augmented retrieval, and policy-level updates."),
            _paper("2404.00003",
                   "Eval-Regulated Self-Improvement: When Should an Agent Trust Its Own Candidates",
                   "2026-03-20", "ICLR 2026", True,
                   "We study evaluation noise in agent self-improvement loops and promotion gating with regression."),
        ],
        "web": [
            _web("self-evolving-skill", "What is a Self-Evolving Skill? A Practical Intro (Tech Blog)",
                 "Skills evolve through feedback-collect -> reflect -> candidate -> eval -> promote loops."),
            _web("agent-memory-self-improvement", "Agent Memory and Self-Improvement: State of the Field",
                 "Review of agent memory architectures and how each supports different kinds of self-improvement."),
        ],
    },
    "agent_memory": {
        "papers": [
            _paper("2501.00011",
                   "Towards a Unified Taxonomy of Agent Memory: Episodic, Semantic and Procedural",
                   "2026-02-10", "arXiv preprint", False,
                   "We propose a taxonomy splitting agent memory into episodic, semantic and procedural strata, "
                   "each with distinct storage and retrieval needs."),
            _paper("2501.00012",
                   "Memory Updating for Agents: When to Revise Instead of Append",
                   "2026-04-01", "ICLR 2026", True,
                   "We compare append-only memory with explicit updating mechanisms under evaluation-driven feedback."),
            _paper("2501.00013",
                   "Retrieval-Augmented Agent Memory in Long-Horizon Tasks",
                   "2026-05-15", "arXiv preprint", False,
                   "A study of retrieval quality, context window integration, and memory updating for long-horizon agents."),
        ],
        "web": [
            _web("agent-memory-survey", "Agent Memory Survey: Storage, Retrieval, Updating (Blog)",
                 "Layered memory (episodic/semantic/procedural), retrieval strategies, and updating policies."),
            _web("memory-vs-context", "Agent Memory vs Context Engineering: Dividing Responsibilities",
                 "How the memory layer and context window share responsibilities in a modern agent harness."),
        ],
    },
    "context_engineering": {
        "papers": [
            _paper("2502.00021",
                   "Context Is a First-Class Citizen: Engineering the Context Window",
                   "2026-01-20", "arXiv preprint", False,
                   "We treat context window management as an engineering discipline: budget, placement and hierarchy."),
            _paper("2502.00022",
                   "Compression in the Loop: Summarization and Token-Level Compression for Agents",
                   "2026-03-05", "ACL 2026", True,
                   "Compare summarization vs token-level compression for keeping agent context within budget."),
            _paper("2502.00023",
                   "Retrieval-Augmented Context Assembly with Memory and Prompt Structure",
                   "2026-04-12", "arXiv preprint", False,
                   "Combine memory retrieval, prompt structure and window allocation for long-running agents."),
        ],
        "web": [
            _web("context-engineering-guide", "Context Engineering: A Practical Guide (Blog)",
                 "Budget, compression, retrieval and hierarchy: how to design what enters the context window."),
            _web("context-harness", "Context Engineering and the Agent Harness",
                 "Where the harness owns context vs where the prompt owns it."),
        ],
    },
    "agent_eval": {
        "papers": [
            _paper("2503.00031",
                   "Beyond Final Answers: Trajectory-Level Evaluation of Agent Behaviour",
                   "2026-02-08", "arXiv preprint", False,
                   "We argue for evaluating full trajectories — tool use, intermediate steps and trace — not just outputs."),
            _paper("2503.00032",
                   "Calibrating LLM-as-a-Judge for Agent Evaluation",
                   "2026-03-18", "NeurIPS 2025", True,
                   "We study judge bias (self-preference, length bias) and propose calibration for agent eval."),
            _paper("2503.00033",
                   "Regression-Driven Agent Development: Evaluation in the Loop",
                   "2026-05-02", "arXiv preprint", False,
                   "A practice paper on wiring evaluation and regression into the agent development cycle."),
        ],
        "web": [
            _web("agent-eval-benchmarks", "Agent Eval Benchmarks: What They Measure and Miss (Blog)",
                 "Survey of agent benchmarks and the shift from final-answer to trajectory evaluation."),
            _web("trace-eval", "Trace as the Unit of Agent Evaluation",
                 "Why execution traces are becoming the primary unit of agent evaluation."),
        ],
    },
    "mcp": {
        "papers": [
            _paper("2504.00041",
                   "MCP at Scale: Protocol Design for Model-Context Connections",
                   "2026-03-22", "arXiv preprint", False,
                   "An analysis of MCP (Model Context Protocol) server/client structure and JSON-RPC tool definitions."),
            _paper("2504.00042",
                   "Tool Protocols and Agent Harnesses: The Rise of MCP",
                   "2026-05-30", "ICML 2026", True,
                   "We study how MCP standardizes tool calling and resource access across agent harnesses."),
            _paper("2504.00043",
                   "Permissions and Audit for MCP Tool Servers",
                   "2026-06-11", "arXiv preprint", False,
                   "Security model for MCP: authorization, scoping and auditability of tool invocations."),
        ],
        "web": [
            _web("mcp-intro", "MCP Explained: Servers, Clients and the Protocol (Blog)",
                 "A walkthrough of MCP server/client architecture and how tools, resources and prompts are exposed."),
            _web("mcp-harness", "MCP and the Modern Agent Harness",
                 "How MCP is becoming the standard interface between agent harnesses and external tools."),
        ],
    },
}


def papers_for(topic: str) -> list[dict]:
    return list(TOPIC_SOURCES.get(topic, TOPIC_SOURCES["self_evolving"])["papers"])


def web_for(topic: str) -> list[dict]:
    return list(TOPIC_SOURCES.get(topic, TOPIC_SOURCES["self_evolving"])["web"])


def all_papers() -> list[dict]:
    out = []
    for t in TOPIC_SOURCES.values():
        out.extend(t["papers"])
    return out


def all_web() -> list[dict]:
    out = []
    for t in TOPIC_SOURCES.values():
        out.extend(t["web"])
    return out


def mock_paper_text(url: str) -> dict:
    for p in all_papers():
        if p["url"] == url:
            return {
                "title": p["title"],
                "url": url,
                "text": f"# {p['title']}\nauthors: Mock Author\npublished: {p['date']}\n\n"
                        f"## Abstract\n{p['snippet']}",
                "truncated": False,
                "note": "mock 离线读取（abstract 级）",
            }
    return {"error": "mock 夹具无此论文", "url": url}


def mock_web_text(url: str) -> dict:
    for w in all_web():
        if w["url"] == url:
            body = f"{w['title']}\n\n{w['snippet']}\n\n（mock 离线网页正文，仅管道验证用。）"
            return {"title": w["title"], "url": url, "text": body, "truncated": False,
                    "note": "mock 离线读取"}
    return {"error": "mock 夹具无此网页", "url": url}
