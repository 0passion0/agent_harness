"""基于 Exa API 的网络检索工具模块；文件与脚本工具在无检索密钥时仍可独立工作。"""

import json
import os

from dotenv import dotenv_values
from langchain_core.tools import tool


def search_api_key() -> str:
    """读取环境变量或本地 .env 中的 Exa 搜索密钥，避免修改全局进程凭据。

    :return: Exa 搜索 API 密钥字符串，若未配置则返回空字符串。
    :rtype: str
    :author: tongliga
    :date: 2026-10-21
    """
    # 第一步：优先读取系统环境变量，未命中时回退尝试加载本地 .env 文件
    return os.getenv("EXA_API_KEY") or dotenv_values(".env").get("EXA_API_KEY") or ""


@tool
async def web_search(query: str) -> str:
    """Search the web for sources. Returns titles, URLs and bounded source text.

    :param query: 检索查询关键词或自然语言问题。
    :type query: str
    :return: 包含标题、网页链接及正文摘要的 JSON 字符串或错误说明文本。
    :rtype: str
    :author: tongliga
    :date: 2026-10-21
    """
    # 第一步：校验检索关键词非空
    if not query.strip():
        return "Search query must not be blank."

    # 第二步：检查并获取 Exa API 密钥
    key = search_api_key()
    if not key:
        return "Web search unavailable: EXA_API_KEY is not configured."
    from langchain_exa import ExaSearchResults

    # 第三步：异步发起 Exa 网页检索并受控截断字符数
    try:
        results = await ExaSearchResults(exa_api_key=key).ainvoke(
            {"query": query, "num_results": 5, "text_contents_options": {"max_characters": 4000}}
        )
        return json.dumps(results, ensure_ascii=False, default=str)
    except Exception as exc:
        # 第四步：捕获异常并返回友好的脱敏错误信息
        return f"Web search failed ({type(exc).__name__}); no sources were retrieved."
