"""Lodestar 配置：默认值 + 环境变量覆盖。不引入任何框架，纯 dataclass。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "lodestar" / "data"
WORKSPACE_DIR = PROJECT_ROOT / "workspace"
DEFAULT_CASES_DIR = PROJECT_ROOT / "lodestar" / "eval" / "cases"
DEFAULT_DB_PATH = DATA_DIR / "lodestar.db"


@dataclass
class Config:
    # --- 运行模式 ---
    llm_mode: str = "live"          # live | mock（mock = 离线夹具，冒烟/回归用）
    search_mode: str = "live"       # live | mock（mock = 检索/读取也走离线夹具，全离线可复现）
    # --- LLM ---
    model: str = "claude-sonnet-5"
    judge_model: str = "claude-haiku-4-5-20251001"
    max_tokens: int = 4000
    judge_max_tokens: int = 1200
    temperature: float = 0.2
    llm_timeout_s: int = 120
    llm_thinking: bool = False   # True=允许思考块；False=传 thinking disabled（更便宜、防空输出）
    # --- Agent Loop 预算（PRD §17，Eval 后续调）---
    max_agent_steps: int = 15
    max_search_queries: int = 8
    max_deep_read_sources: int = 5
    max_replans: int = 2              # assess 证据不足时最多补搜几轮（v0.1.10：1→2）
    rerank_top_n: int = 8           # Rerank 输出 Top N；真正深度读取取前 max_deep_read_sources
    rerank_min_score: int = 5       # 噪声过滤：分数低于此的来源不进 Brief Key Sources（1-10）
    web_results_per_query: int = 6
    arxiv_results_per_query: int = 6
    arxiv_search_field: str = "abs"    # arXiv 检索字段：all=全文（噪声多）| abs=摘要（精确）| ti=标题
    read_char_budget: int = 12000   # 单源读取字符上限（硬截断）
    tool_timeout_s: int = 30
    # --- 产品 ---
    brief_language: str = "zh"      # zh | en
    web_search_backend: str = "duckduckgo"
    # --- V1-R1：venue 元数据回填（免费无 Key；provider 顺序回退）---
    enrich_venues: bool = True       # 检索去重后回填 journal/venue
    venue_providers: tuple = ("semanticscholar", "openalex", "dblp", "crossref")  # 按序尝试，前一个成功即停
    venue_enrich_limit: int = 10     # 单次任务最多回填篇数（防限流）
    venue_request_interval_s: float = 1.2   # 无 Key 约 1 QPS，节流间隔
    venue_user_agent: str = "Lodestar/0.1 (research workspace; mailto:lodestar.research.dev@example.com)"
    # --- V1-R2：PDF 全文读取 ---
    pdf_cache_dir: Path = WORKSPACE_DIR / "pdfs_cache"   # PDF 下载缓存（gitignore）
    full_text_enabled: bool = False       # 默认关：全文读取成本高；开启后仅 Top N 源读全文
    full_text_max_sources: int = 2        # 开启时最多几个来源读全文（token 预算守护）
    # --- V3：Build executor（Coding Agent CLI）---
    build_executor: str = "codex"         # codex | claude | auto（选型见 docs §七）
    codex_model: str = "deepseek-v4-flash"
    codex_provider_name: str = "lodestar-gw"
    codex_base_url: str = ""              # 空=用用户自带 codex 配置（ChatGPT 登录）；设了则走内网网关（Responses API）
    codex_require_gateway: bool = True    # 保险：未配 BASE_URL 时拒绝用 codex 默认模式（防误烧 ChatGPT Plus 额度）
    # codex 网关模式的 key 走 LODESTAR_CODEX_API_KEY（.env，gitignore），不在代码里
    # --- 存储 ---
    db_path: Path = DEFAULT_DB_PATH
    workspace_dir: Path = WORKSPACE_DIR
    cases_dir: Path = DEFAULT_CASES_DIR

    def ensure_dirs(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.workspace_dir.mkdir(parents=True, exist_ok=True)


def load_config() -> Config:
    """从 .env 与环境变量加载覆盖项（仅覆盖有值的键）。"""
    load_dotenv(PROJECT_ROOT / ".env")

    c = Config()
    c.llm_mode = os.getenv("LODESTAR_LLM_MODE", c.llm_mode)
    c.model = os.getenv("LODESTAR_MODEL", c.model)
    c.judge_model = os.getenv("LODESTAR_JUDGE_MODEL", c.judge_model)
    c.llm_thinking = os.getenv("LODESTAR_LLM_THINKING", str(c.llm_thinking)).lower() in {"1", "true", "yes", "on"}
    c.brief_language = os.getenv("LODESTAR_BRIEF_LANGUAGE", c.brief_language)
    c.web_search_backend = os.getenv("LODESTAR_WEB_SEARCH_BACKEND", c.web_search_backend)
    c.enrich_venues = os.getenv("LODESTAR_ENRICH_VENUES", str(c.enrich_venues)).lower() in {"1", "true", "yes", "on"}
    if os.getenv("LODESTAR_TOOL_TIMEOUT"):
        c.tool_timeout_s = int(os.environ["LODESTAR_TOOL_TIMEOUT"])
    c.full_text_enabled = os.getenv("LODESTAR_FULL_TEXT", str(c.full_text_enabled)).lower() in {"1", "true", "yes", "on"}
    c.arxiv_search_field = os.getenv("LODESTAR_ARXIV_SEARCH_FIELD", c.arxiv_search_field)
    if os.getenv("LODESTAR_RERANK_MIN_SCORE"):
        c.rerank_min_score = int(os.environ["LODESTAR_RERANK_MIN_SCORE"])
    if os.getenv("LODESTAR_FULL_TEXT_MAX_SOURCES"):
        c.full_text_max_sources = int(os.environ["LODESTAR_FULL_TEXT_MAX_SOURCES"])
    c.build_executor = os.getenv("LODESTAR_BUILD_EXECUTOR", c.build_executor)
    c.codex_model = os.getenv("LODESTAR_CODEX_MODEL", c.codex_model)
    c.codex_base_url = os.getenv("LODESTAR_CODEX_BASE_URL", c.codex_base_url)
    c.codex_require_gateway = os.getenv("LODESTAR_CODEX_REQUIRE_GATEWAY", str(c.codex_require_gateway)).lower() not in {"0", "false", "no", "off"}
    if os.getenv("LODESTAR_VENUE_PROVIDERS"):
        c.venue_providers = tuple(p.strip() for p in os.environ["LODESTAR_VENUE_PROVIDERS"].split(",") if p.strip())
    if os.getenv("LODESTAR_VENUE_USER_AGENT"):
        c.venue_user_agent = os.environ["LODESTAR_VENUE_USER_AGENT"]
    if os.getenv("LODESTAR_DB_PATH"):
        c.db_path = Path(os.environ["LODESTAR_DB_PATH"])
    if os.getenv("LODESTAR_WORKSPACE_DIR"):
        c.workspace_dir = Path(os.environ["LODESTAR_WORKSPACE_DIR"])
    if os.getenv("LODESTAR_CASES_DIR"):
        c.cases_dir = Path(os.environ["LODESTAR_CASES_DIR"])
    c.ensure_dirs()
    return c
