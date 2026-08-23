from lodestar.tools.registry import TOOLS, call_tool, describe_tools, register

# 注册内置工具（副作用：填充 TOOLS 注册表）
from lodestar.tools import (  # noqa: E402,F401
    arxiv_search,
    knowledge,
    paper_read,
    project_context,
    web_read,
    web_search,
)

__all__ = ["TOOLS", "register", "call_tool", "describe_tools"]
