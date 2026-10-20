import asyncio

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import Field

from vertical_harness import HarnessSettings, RunContext
from vertical_harness.engine import compose, routing


class RecordingModel(FakeMessagesListChatModel):
    seen_tools: list = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        self.seen_tools.append([t.name for t in tools])
        return self


def settings(root, **kwargs):
    return HarnessSettings(
        _env_file=None,
        model="test:model",
        workspace_root=root,
        store_backend="memory",
        enabled_plugins=[],
        enable_memory=False,
        enable_planning=False,
        **kwargs,
    )


@pytest.mark.parametrize("mode", ["sync", "async", "stream"])
@pytest.mark.parametrize("tier", ["economy", "quality"])
def test_model_router_supports_native_execution_paths(tmp_path, monkeypatch, mode, tier):
    economy = RecordingModel(responses=[AIMessage(content="economy")])
    quality = RecordingModel(responses=[AIMessage(content="quality")])
    monkeypatch.setattr(compose, "init_configured_model", lambda *_: quality)
    monkeypatch.setattr(
        routing,
        "init_configured_model",
        lambda _, name: economy if name == "test:cheap" else quality,
    )
    agent = compose.build_agent(
        settings(tmp_path, economy_model="test:cheap", quality_model="test:quality")
    )
    inputs = {"messages": [HumanMessage(content="hello")]}
    kwargs = {
        "config": {"configurable": {"thread_id": mode}},
        "context": RunContext("t", "u", tier),
    }

    async def invoke():
        if mode == "stream":
            values = [
                value async for value in agent.astream(inputs, **kwargs, stream_mode="values")
            ]
            return values[-1]
        return await agent.ainvoke(inputs, **kwargs)

    result = agent.invoke(inputs, **kwargs) if mode == "sync" else asyncio.run(invoke())
    assert result["messages"][-1].content == tier


@pytest.mark.parametrize("asynchronous", [False, True])
def test_private_database_cannot_be_read_or_deleted(tmp_path, monkeypatch, asynchronous):
    config = HarnessSettings(
        _env_file=None,
        model="test:model",
        workspace_root=tmp_path,
        store_db_path=tmp_path / "store.sqlite",
        enabled_plugins=[],
        enable_memory=False,
        enable_planning=False,
    )
    config.resolved_store_target.write_text("private sentinel", encoding="utf-8")
    model = RecordingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "read_file", "args": {"file_path": "/store.sqlite"}, "id": "read"},
                    {"name": "delete", "args": {"file_path": "/store.sqlite"}, "id": "delete"},
                ],
            ),
            AIMessage(content="done"),
        ]
    )
    monkeypatch.setattr(compose, "init_configured_model", lambda *_: model)
    kwargs = {"config": {"configurable": {"thread_id": "private"}}, "context": RunContext("t", "u")}
    inputs = {"messages": [HumanMessage(content="try files")]}

    async def run():
        async with compose.aopen_agent(config) as agent:
            return await agent.ainvoke(inputs, **kwargs)

    if asynchronous:
        result = asyncio.run(run())
    else:
        with compose.open_agent(config) as agent:
            result = agent.invoke(inputs, **kwargs)
    errors = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert len(errors) == 2 and all("permission denied" in str(m.content).lower() for m in errors)
    assert config.resolved_store_target.exists()
