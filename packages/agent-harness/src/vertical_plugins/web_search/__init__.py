"""通过现有插件契约提供可选的 Exa 搜索。"""

from dotenv import load_dotenv
from langchain_core.tools import tool
from langchain_exa import ExaSearchResults

from vertical_plugins.common.base import DomainPlugin
from vertical_plugins.common.contracts import (
    BuildServices,
    PluginContribution,
    PluginManifest,
    ToolContribution,
)


@tool
async def exa_search_results_json(query: str) -> str:
    """Search the web with Exa for relevant sources. Requires EXA_API_KEY."""
    load_dotenv(".env")
    return await ExaSearchResults().ainvoke({"query": query})


class WebSearchPlugin(DomainPlugin):
    """声明搜索工具，实际执行时才创建 Exa 客户端。"""

    manifest = PluginManifest(
        plugin_id="web.search", api_version=1, version="1.0.0",
        description="Search the web with Exa (requires EXA_API_KEY)",
    )

    def build(self, services: BuildServices) -> PluginContribution:
        """向宿主注册 LangChain 原生搜索工具。"""
        return PluginContribution(tools=[ToolContribution(tool=exa_search_results_json)])
