import asyncio

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import Field

from vertical_harness.config import HarnessSettings
from vertical_harness.engine import compose


@pytest.mark.parametrize("use_async", [False, True])
def test_run_can_continue_past_thirty_model_calls(monkeypatch, tmp_path, use_async):

    class TrackingModel(FakeMessagesListChatModel):
        tool_bindings: list = Field(default_factory=list)

        def bind_tools(self, tools, *, tool_choice=None, **kwargs):
            self.tool_bindings.append((list(tools), tool_choice))
            return self

    @tool
    def echo(value: str) -> str:
        """Return the provided value."""
        return value

    model = TrackingModel(
        responses=[
            *[
                AIMessage(
                    content="",
                    tool_calls=[{"name": "ls", "args": {"path": "/"}, "id": f"echo-{i}"}],
                )
                for i in range(32)
            ],
            AIMessage(content="All 32 tool calls completed."),
            AIMessage(content="Follow-up complete."),
        ]
    )
    monkeypatch.setattr(compose.ModelRouting, "init_configured_model", lambda settings: model)
    settings = HarnessSettings(
        _env_file=None,
        model="test:model",
        workspace_root=tmp_path,
        store_backend="memory",
        enable_memory=False,
        enable_planning=False,
    )
    agent = compose.build_agent(settings, checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "unlimited-model-calls"}}
    if use_async:

        async def run():
            first = await agent.ainvoke(
                {"messages": [HumanMessage(content="Run all tools")]}, config
            )
            second = await agent.ainvoke({"messages": [HumanMessage(content="Follow up")]}, config)
            return (first, second)

        first, second = asyncio.run(run())
    else:
        first = agent.invoke({"messages": [HumanMessage(content="Run all tools")]}, config)
        second = agent.invoke({"messages": [HumanMessage(content="Follow up")]}, config)
    assert first["messages"][-1].content == "All 32 tool calls completed."
    assert sum(isinstance(message, ToolMessage) for message in first["messages"]) == 32
    assert second["messages"][-1].content == "Follow-up complete."
    assert len(model.tool_bindings) == 34
    assert all((tools and choice != "none" for tools, choice in model.tool_bindings))
    assert "run_model_call_count" not in second
