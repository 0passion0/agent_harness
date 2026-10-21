"""基于 Exa 搜索引擎的网络网页检索智能体工具模块。"""

import json
import os

from dotenv import dotenv_values
from langchain_core.tools import tool


class WebSearch:
    """网络检索工具类，封装针对 Exa 检索服务的异步检索调用。

    :author: tongliga
    :date: 2026-10-21
    """

    @staticmethod
    def search_api_key() -> str:
        """从操作系统环境变量或当前工作区 .env 文件安全读取 EXA_API_KEY。

        :return: 解析获得的 Exa API 密钥字符串，未配置时返回空字符串。
        :rtype: str
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：优先读取系统环境变量，若不存在则降级读取 .env 文件
        return os.getenv("EXA_API_KEY") or dotenv_values(".env").get("EXA_API_KEY") or ""

    @staticmethod
    @tool
    async def web_search(query: str) -> str:
        """Search the web for sources. Returns titles, URLs and bounded source text.

        :param query: 搜索查询关键词或自然语言问题。
        :type query: str
        :return: 包含网页标题、URL 及正文片段的 JSON 字符串或错误说明。
        :rtype: str
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：校验查询字符串非空
        if not query.strip():
            return "Search query must not be blank."

        # 第二步：获取 API 密钥，未配置时友好返回错误信息而非让智能体崩溃
        key = WebSearch.search_api_key()
        if not key:
            return "Web search unavailable: EXA_API_KEY is not configured."
        from langchain_exa import ExaSearchResults

        # 第三步：异步调用 Exa 检索服务并序列化返回结果
        try:
            results = await ExaSearchResults(exa_api_key=key).ainvoke(
                {"query": query, "num_results": 5, "text_contents_options": {"max_characters": 4000}}
            )
            return json.dumps(results, ensure_ascii=False, default=str)
        except Exception as exc:
            return f"Web search failed ({type(exc).__name__}); no sources were retrieved."


search_api_key = WebSearch.search_api_key
web_search = WebSearch.web_search

__all__ = ["WebSearch", "search_api_key", "web_search"]
