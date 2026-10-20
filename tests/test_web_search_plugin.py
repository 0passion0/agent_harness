import asyncio

from vertical_harness import HarnessSettings, build_agent
from vertical_plugins.common.contracts import BuildServices
from vertical_plugins.web_search import WebSearchPlugin


def test_search_is_enabled_by_default_without_creating_a_client(tmp_path, monkeypatch):
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage

    from vertical_harness.engine import compose
    from vertical_plugins import web_search

    monkeypatch.delenv("EXA_API_KEY", raising=False)

    def unexpected_client():
        raise AssertionError("Search clients must not be created during agent construction")

    monkeypatch.setattr(web_search, "ExaSearchResults", unexpected_client)

    class Model(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            assert any(getattr(item, "name", None) == "exa_search_results_json" for item in tools)
            return self

    monkeypatch.setattr(compose, "init_configured_model", lambda _: Model(responses=[AIMessage(content="done")]))
    settings = HarnessSettings(_env_file=None, model="test:core", workspace_root=tmp_path,
                               store_backend="memory", enable_memory=False)
    assert settings.enabled_plugins == ["web.search"]
    agent = build_agent(settings)
    assert agent.invoke({"messages": [{"role": "user", "content": "hello"}]},
                        {"configurable": {"thread_id": "core"}})["messages"][-1].content == "done"

    contribution = WebSearchPlugin().build(BuildServices(workspace_root=tmp_path))
    assert [item.name for item in contribution.tools] == ["exa_search_results_json"]

    from vertical_harness.desktop_runtime import plugin_catalog

    class Search:
        async def ainvoke(self, args):
            assert args == {"query": "evidence"}
            return "verified sources"

    monkeypatch.setattr(web_search, "ExaSearchResults", Search)
    assert "web.search" in {item["id"] for item in plugin_catalog()["plugins"]}
    assert asyncio.run(contribution.tools[0].tool.ainvoke({"query": "evidence"})) == "verified sources"
