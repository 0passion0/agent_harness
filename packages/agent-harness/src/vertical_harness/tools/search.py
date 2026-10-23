"""基于 Exa 搜索引擎的网络网页检索智能体工具模块。"""

import json
import os

from dotenv import dotenv_values
from langchain_core.tools import tool
from pydantic import SecretStr


class WebSearch:
    """网络检索工具类，封装针对 Exa 检索服务的异步检索调用。"""

    @staticmethod
    def search_api_key() -> str:
        """从操作系统环境变量或当前工作区 .env 文件安全读取 EXA_API_KEY。"""
        return os.getenv("EXA_API_KEY") or dotenv_values(".env").get("EXA_API_KEY") or ""

    @staticmethod
    @tool
    async def web_search(query: str) -> str:
        """Search the web for sources. Returns titles, URLs and bounded source text."""
        if not query.strip():
            return "Search query must not be blank."
        key = WebSearch.search_api_key()
        if not key:
            return "Web search unavailable: EXA_API_KEY is not configured."
        from langchain_exa import ExaSearchResults

        try:
            results = await ExaSearchResults(exa_api_key=SecretStr(key)).ainvoke(
                {
                    "query": query,
                    "num_results": 5,
                    "text_contents_options": {"max_characters": 4000},
                }
            )
            return json.dumps(results, ensure_ascii=False, default=str)
        except Exception as exc:
            return f"Web search failed ({type(exc).__name__}); no sources were retrieved."
