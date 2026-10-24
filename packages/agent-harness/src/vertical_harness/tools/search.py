"""基于 Exa 搜索引擎的网络网页检索智能体工具模块。

本模块提供 `WebSearch` 工具类：
1. 从环境变量或 `.env` 文件安全读取 `EXA_API_KEY`；
2. 动态按需加载 `langchain_exa.ExaSearchResults`；
3. 执行异步网页检索，限制返回最多 5 个权威来源，每个页面截取最多 4000 字符文本，
   输出标准 JSON 格式结果。
"""

from __future__ import annotations

import json
import os

from dotenv import dotenv_values
from langchain_core.tools import tool
from pydantic import SecretStr


class WebSearch:
    """网络检索工具类，封装针对 Exa 检索服务的异步检索调用。

    静态方法工具集合，供智能体在需要获取最新互联网信息、文档或代码事实时调用。
    """

    @staticmethod
    def search_api_key() -> str:
        """从操作系统环境变量或当前工作区 .env 文件安全读取 EXA_API_KEY。

        Returns:
            str: 解析出的 API 密钥字符串；若未配置则返回空字符串。
        """
        return os.getenv("EXA_API_KEY") or dotenv_values(".env").get("EXA_API_KEY") or ""

    @staticmethod
    @tool
    async def web_search(query: str) -> str:
        """Search the web for sources. Returns titles, URLs and bounded source text.

        使用 Exa 搜索引擎异步检索互联网权威网页来源，返回包含标题、链接及受限网页片段的 JSON 字符串。

        Args:
            query (str): 目标检索的搜索词或查询句子（不可为空白）。

        Returns:
            str: 格式化为 JSON 字符串的搜索结果列表，包含网页标题、URL 及最多 4,000 字符正文；
                若密钥未配置或请求失败则返回清晰的错误描述字符串。
        """
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
