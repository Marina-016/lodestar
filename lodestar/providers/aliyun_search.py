"""Alibaba's official WebSearch MCP via the official MCP Python SDK.

Only query text is sent. Model inference and built-in enable_search are separate.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from lodestar.providers.search_quota import QuotaUnavailable, reserve

ENDPOINT = 'https://dashscope.aliyuncs.com/api/v1/mcps/WebSearch/mcp'


class SearchUnavailable(ValueError):
    pass


async def _invoke(key: str, timeout: int, query: str | None, max_results: int = 6):
    # Optional dependency: offline and keyless installations do not need MCP.
    try:
        import anyio
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
        from jsonschema import validate
    except ImportError:
        raise SearchUnavailable('请先安装官方 MCP SDK：pip install -e ".[web-search]"') from None
    with anyio.fail_after(timeout):
        async with streamablehttp_client(ENDPOINT,
                headers={'Authorization': 'Bearer ' + key},
                timeout=timeout, sse_read_timeout=timeout) as (read, write, _):
            async with ClientSession(read, write,
                    read_timeout_seconds=timedelta(seconds=timeout)) as session:
                await session.initialize()
                listed = await session.list_tools()
                if query is None:
                    # Diagnostic metadata only; never invoke a search here.
                    return {'tools': [tool.model_dump(exclude_none=True) for tool in listed.tools]}
                candidates = [tool for tool in listed.tools
                              if tool.inputSchema.get('properties', {}).get('query', {}).get('type') == 'string']
                if len(candidates) != 1:
                    raise SearchUnavailable('官方搜索工具定义已变化，请核对 tools/list 后再接入')
                tool = candidates[0]
                arguments = {'query': query}
                if tool.inputSchema.get('properties', {}).get('count', {}).get('type') == 'integer':
                    arguments['count'] = max_results
                validate(arguments, tool.inputSchema)
                result = await session.call_tool(tool.name, arguments)
                if result.isError:
                    raise SearchUnavailable('官方搜索工具返回错误，未自动重试；请核对开通、凭据及额度')
                return result.model_dump(exclude_none=True)


def request(query: str | None, *, quota_path: Path, timeout: int = 30, max_results: int = 6) -> dict:
    key = os.getenv('DASHSCOPE_API_KEY', '').strip()
    if not key:
        raise SearchUnavailable('缺少本地 DASHSCOPE_API_KEY')
    # Reserve before ANY network request. Failed negotiation/transport is counted
    # conservatively too, so an ambiguous result cannot cause a free retry.
    if query is not None:
        reserve(quota_path, key)
    try:
        return asyncio.run(_invoke(key, max(1, min(timeout, 60)), query, max_results))
    except (QuotaUnavailable, SearchUnavailable):
        raise
    except Exception:
        # Provider exceptions can contain request metadata. Do not serialize them
        # into traces or model context, and never automatically repeat tools/call.
        raise SearchUnavailable('官方搜索 MCP 连接或响应失败；请核对服务开通、通用 API Key 和免费额度') from None


def normalize(result: dict, query: str, max_results: int) -> dict:
    """Keep MCP text as evidence; extract explicitly supplied JSON source records.

    No invented URLs/titles/dates, and no model-written summary masquerading as a
    source. Unknown text remains visible to the model and is labelled explicitly.
    """
    texts = [block['text'] for block in result.get('content', [])
             if block.get('type') == 'text' and isinstance(block.get('text'), str)]
    payloads = [result['structuredContent']] if result.get('structuredContent') is not None else []
    for value in texts:
        try:
            payloads.append(json.loads(value))
        except ValueError:
            pass
    sources, seen = [], set()

    def visit(value, depth=0):
        if depth > 8 or len(sources) >= max_results:
            return
        if isinstance(value, list):
            for child in value[:100]:
                visit(child, depth + 1)
        elif isinstance(value, dict):
            url, title = value.get('url'), value.get('title')
            try:
                parsed = urlsplit(url) if isinstance(url, str) else None
            except ValueError:
                parsed = None
            if parsed and parsed.scheme in {'http', 'https'} and parsed.hostname and isinstance(title, str) and title.strip() and url not in seen:
                seen.add(url)
                snippet = next((value[k] for k in ('snippet', 'description', 'content')
                                if isinstance(value.get(k), str)), '')
                sources.append({'source_type': 'web', 'url': url, 'title': title[:300],
                    'snippet': snippet[:2000], 'authors': [], 'date': '',
                    'dedup_key': 'url:' + url, 'provider': 'aliyun_websearch_mcp', 'query': query})
            for child in value.values():
                if isinstance(child, (dict, list)):
                    visit(child, depth + 1)

    for payload in payloads:
        visit(payload)
    text = '\n'.join(texts)
    if not text and payloads:
        text = json.dumps(payloads, ensure_ascii=False)
    if not text and not sources:
        raise SearchUnavailable('官方搜索返回了无法读取的内容，未视为成功的空结果')
    return {'sources': sources, 'query': query, 'provider': 'aliyun_websearch_mcp',
            'provider_text': text[:24000],
            'note': f'官方 WebSearch MCP 返回 {len(sources)} 条可识别来源；搜索摘要不等于已阅读网页正文。'
                    if sources else '官方 MCP 原始搜索文本；未识别到结构化来源，不据此虚构来源卡片。'}
