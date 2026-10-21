"""Exa web search tool; file and script tools work without a search key."""

import json
import os

from dotenv import dotenv_values
from langchain_core.tools import tool


class WebSearch:
    @staticmethod
    def search_api_key() -> str:
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
            results = await ExaSearchResults(exa_api_key=key).ainvoke(
                {"query": query, "num_results": 5, "text_contents_options": {"max_characters": 4000}}
            )
            return json.dumps(results, ensure_ascii=False, default=str)
        except Exception as exc:
            return f"Web search failed ({type(exc).__name__}); no sources were retrieved."


search_api_key = WebSearch.search_api_key
web_search = WebSearch.web_search
