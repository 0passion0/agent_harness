"""Exercise deterministic tool-result pruning before summarization."""

from __future__ import annotations

import asyncio

from deepagents.backends import FilesystemBackend
from langchain.agents.middleware import ModelRequest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from vertical_harness.middleware.context_pruning import ContextPruningMiddleware


def history(results: int = 6, size: int = 20_000) -> list:
    """Build alternating shell tool calls whose results exceed any small budget."""
    messages: list = [HumanMessage(content="Inspect the workspace.")]
    for index in range(results):
        messages.append(
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "shell",
                        "args": {"command": f"run-{index}"},
                        "id": f"call-{index}",
                        "type": "tool_call",
                    }
                ],
            )
        )
        messages.append(
            ToolMessage(content=f"RESULT-{index} " + "x" * size, tool_call_id=f"call-{index}")
        )
    return messages


def request_for(messages: list) -> ModelRequest:
    """Wrap messages in a request whose model advertises a large context window."""
    return ModelRequest(
        model=FakeMessagesListChatModel(responses=[], profile={"max_input_tokens": 1_000_000}),
        messages=messages,
        tools=[],
    )


def test_pruning_leaves_request_untouched_below_threshold(tmp_path) -> None:
    backend = FilesystemBackend(root_dir=tmp_path)
    request = request_for(history(results=2, size=1_000))
    seen = []
    ContextPruningMiddleware(backend=backend, maximum=100_000).wrap_model_call(
        request, lambda item: seen.append(item)
    )
    assert seen[0] is request
    assert not (tmp_path / "large_tool_results").exists()


def test_pruning_replaces_old_results_and_keeps_recent_ones(tmp_path) -> None:
    backend = FilesystemBackend(root_dir=tmp_path)
    messages = history()
    request = request_for(messages)
    seen = []
    ContextPruningMiddleware(backend=backend, maximum=20_000).wrap_model_call(
        request, lambda item: seen.append(item)
    )
    pruned = seen[0].messages
    assert len(pruned) == len(messages)
    for index in (8, 10, 12):
        assert pruned[index].content == messages[index].content
    for index in (2, 4, 6):
        assert "/large_tool_results/call-" in pruned[index].content
        assert len(pruned[index].content) < len(messages[index].content)
        assert messages[index].content.startswith(f"RESULT-{index // 2 - 1} ")
    archived = backend.read("/large_tool_results/call-0")
    assert archived.file_data["content"].startswith("RESULT-0 ")


def test_pruning_matches_the_async_path(tmp_path) -> None:
    backend = FilesystemBackend(root_dir=tmp_path)
    messages = history()
    request = request_for(messages)
    seen = []

    async def handler(item):
        seen.append(item)

    middleware = ContextPruningMiddleware(backend=backend, maximum=20_000)
    asyncio.run(middleware.awrap_model_call(request, handler))
    assert "/large_tool_results/call-0" in seen[0].messages[2].content
    assert seen[0].messages[12].content == messages[12].content


def test_pruning_keeps_checkpoint_history_intact(tmp_path, monkeypatch) -> None:
    from pydantic import Field

    from vertical_harness.config import HarnessSettings
    from vertical_harness.core import compose

    class RecordingModel(FakeMessagesListChatModel):
        seen: list = Field(default_factory=list)

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, *args, **kwargs):
            self.seen.append(list(messages))
            return super()._generate(messages, *args, **kwargs)

    model = RecordingModel(
        responses=[AIMessage(content="History summary."), AIMessage(content="Done.")],
        profile={"max_input_tokens": 1_000_000},
    )
    monkeypatch.setattr(compose.ModelRouting, "init_configured_model", lambda _settings: model)
    agent = compose.AgentComposer.build_agent(
        HarnessSettings.model_validate(
            {
                **(
                    HarnessSettings(
                        _env_file=None,
                        model="test:pruning",
                        workspace_root=tmp_path,
                        store_backend="memory",
                        enable_planning=False,
                    )
                ).model_dump(),
                **{"max_context_tokens": 20_000},
            }
        ),
    )
    config = {"configurable": {"thread_id": "pruning-checkpoint"}}

    result = agent.invoke({"messages": history()}, config)

    assert result["messages"][-1].content == "Done."
    assert "/large_tool_results/call-0" in repr(model.seen[0])
    checkpoint = agent.get_state(config).values["messages"]
    assert checkpoint[2].content.startswith("RESULT-0 ")
