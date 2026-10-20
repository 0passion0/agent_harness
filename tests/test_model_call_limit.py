import asyncio

import pytest
from langchain.agents import create_agent
from langchain.agents.middleware import ModelRequest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import Field

from vertical_harness.engine.policy import FinalModelCallLimitMiddleware


@pytest.mark.parametrize("use_async", [False, True])
def test_final_allowed_model_call_summarizes_without_tools(use_async):
    middleware = FinalModelCallLimitMiddleware(run_limit=3)
    request = ModelRequest(
        model=FakeMessagesListChatModel(responses=[]),
        messages=[HumanMessage(content="Verified result")],
        system_message=SystemMessage(content="Original instructions."),
        tools=[{"name": "run_python"}],
        state={"messages": [], "run_model_call_count": 2},
    )
    received = []

    if use_async:
        async def invoke():
            async def handler(final):
                return _record(received, final)

            return await middleware.awrap_model_call(request, handler)

        asyncio.run(invoke())
    else:
        middleware.wrap_model_call(request, lambda final: _record(received, final))

    final = received[0]
    assert final.tools == []
    assert final.tool_choice == "none"
    assert "Original instructions." in final.system_prompt
    assert "verified files" in final.system_prompt
    assert request.tools == [{"name": "run_python"}]
    assert request.system_prompt == "Original instructions."


def test_model_call_before_final_slot_keeps_request_unchanged():
    middleware = FinalModelCallLimitMiddleware(run_limit=3)
    request = ModelRequest(
        model=FakeMessagesListChatModel(responses=[]),
        messages=[HumanMessage(content="Continue")],
        system_message=SystemMessage(content="Original instructions."),
        tools=[{"name": "run_python"}],
        state={"messages": [], "run_model_call_count": 1},
    )
    middleware.wrap_model_call(request, lambda passed: passed)

    assert request.tools == [{"name": "run_python"}]
    assert request.system_prompt == "Original instructions."


def test_final_call_preserves_native_run_limit_counting():
    middleware = FinalModelCallLimitMiddleware(run_limit=3)

    assert middleware.after_model({"run_model_call_count": 2, "thread_model_call_count": 4}, None) == {
        "thread_model_call_count": 5,
        "run_model_call_count": 3,
    }
    exhausted = middleware.before_model(
        {"run_model_call_count": 3, "thread_model_call_count": 5}, None
    )
    assert exhausted["jump_to"] == "end"
    assert isinstance(exhausted["messages"][0], AIMessage)
    assert "Model call limits exceeded" in exhausted["messages"][0].content


def test_composed_graph_uses_final_response_and_resets_budget_per_turn():
    class TrackingModel(FakeMessagesListChatModel):
        tool_bindings: list = Field(default_factory=list)
        untooled_bindings: int = 0

        def bind_tools(self, tools, *, tool_choice=None, **kwargs):
            self.tool_bindings.append((list(tools), tool_choice))
            return self

        def bind(self, **kwargs):
            self.untooled_bindings += 1
            return super().bind(**kwargs)

    @tool
    def echo(value: str) -> str:
        """Return the provided value."""
        return value

    model = TrackingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[{"name": "echo", "args": {"value": "verified"}, "id": "echo-1"}],
            ),
            AIMessage(content="Turn one is complete."),
            AIMessage(
                content="",
                tool_calls=[{"name": "echo", "args": {"value": "verified"}, "id": "echo-2"}],
            ),
            AIMessage(content="Turn two is complete."),
        ]
    )
    agent = create_agent(
        model=model,
        tools=[echo],
        middleware=[FinalModelCallLimitMiddleware(run_limit=2)],
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": "call-limit-turn-reset"}}

    first = agent.invoke({"messages": [HumanMessage(content="Run turn one")]}, config)
    second = agent.invoke({"messages": [HumanMessage(content="Run turn two")]}, config)

    assert first["messages"][-1].content == "Turn one is complete."
    assert second["messages"][-1].content == "Turn two is complete."
    assert len(model.tool_bindings) == 2
    assert model.untooled_bindings == 2


def _record(received, request):
    received.append(request)
    return request
