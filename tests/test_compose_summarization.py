from __future__ import annotations

import pytest
from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import Field

from vertical_harness.engine.compose import _summarization_middleware


class ToolBindableFakeModel(FakeListChatModel):
    seen_messages: list = Field(default_factory=list)

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen_messages.append(messages)
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def test_native_summarization_compacts_model_view_and_preserves_checkpoint_history(
    tmp_path,
) -> None:
    model = ToolBindableFakeModel(
        responses=["Summary retains the user's goal and verified evidence.", "Done."],
        profile={"max_input_tokens": 100_000},
    )
    backend = FilesystemBackend(root_dir=tmp_path)
    middleware = _summarization_middleware(model, backend)

    # Large old tool input forces argument truncation; long history crosses the early trigger.
    old_args = "OLD_ARG_SENTINEL " + ("x" * 40_000)
    history = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "write_file",
                    "args": {"content": old_args, "file_path": "/old.txt"},
                    "id": "old-call",
                    "type": "tool_call",
                }
            ],
        ),
        ToolMessage(content="old inspection complete", tool_call_id="old-call"),
        *[
            message
            for i in range(8)
            for message in (
                HumanMessage(content=f"Earlier request {i}. " + "context " * 4_000),
                AIMessage(content=f"Verified result {i} was recorded."),
            )
        ],
    ]
    history.extend(
        [
            HumanMessage(content="Current check: retain this result."),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_file",
                        "args": {"payload": "recent call"},
                        "id": "recent-call",
                        "type": "tool_call",
                    }
                ],
            ),
            ToolMessage(content="CURRENT_TOOL_RESULT_SENTINEL", tool_call_id="recent-call"),
        ]
    )
    agent = create_deep_agent(
        model=model,
        backend=backend,
        middleware=[middleware],
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": "summary-test"}}

    result = agent.invoke({"messages": history}, config=config)

    snapshot = agent.get_state(config)
    event = snapshot.values["_summarization_event"]
    assert event["file_path"]
    checkpoint_messages = snapshot.values["messages"]
    old_call = next(
        call
        for message in checkpoint_messages
        for call in getattr(message, "tool_calls", [])
        if call["id"] == "old-call"
    )
    assert old_call["args"]["content"] == old_args
    archived = backend.read(event["file_path"], offset=0, limit=20_000)
    archived_content = archived.file_data["content"]
    assert "OLD_ARG_SENTINEL" in archived_content
    assert "x" * 1_500 not in archived_content
    model_request = repr(model.seen_messages[-1])
    assert "CURRENT_TOOL_RESULT_SENTINEL" in model_request
    assert "recent-call" in model_request
    assert "Summary retains the user's goal" in model_request
    assert any(
        getattr(message, "content", None) == "CURRENT_TOOL_RESULT_SENTINEL"
        for message in checkpoint_messages
    )
    assert result["messages"][-1].content == "Done."


def test_completed_script_is_trimmed_before_the_next_model_call(tmp_path) -> None:
    model = ToolBindableFakeModel(responses=["Done."], profile={"max_input_tokens": 100_000})
    backend = FilesystemBackend(root_dir=tmp_path)
    script = "print('saved')\n" * 3_000
    (tmp_path / "analysis.py").write_text(script, encoding="utf-8")
    history = [
        HumanMessage(content="Analyze the supplied table."),
        AIMessage(content="", tool_calls=[{
            "name": "write_file", "args": {"file_path": "/analysis.py", "content": script},
            "id": "saved-script", "type": "tool_call",
        }]),
        ToolMessage(content="Updated file /analysis.py", tool_call_id="saved-script"),
    ]
    agent = create_deep_agent(
        model=model, backend=backend, middleware=[_summarization_middleware(model, backend)],
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": "saved-script-test"}}

    agent.invoke({"messages": history}, config=config)

    assert len(model.seen_messages) == 1
    assert "print('saved')\n" * 100 not in repr(model.seen_messages[0])
    assert "Updated file /analysis.py" in repr(model.seen_messages[0])
    saved_call = agent.get_state(config).values["messages"][1].tool_calls[0]
    assert saved_call["args"]["content"] == script
    assert (tmp_path / "analysis.py").read_text(encoding="utf-8") == script


def test_summarization_thresholds_are_early_and_respect_small_model_profile(tmp_path) -> None:
    large = FakeListChatModel(responses=["ok"], profile={"max_input_tokens": 100_000})
    large_middleware = _summarization_middleware(large, FilesystemBackend(root_dir=tmp_path))
    assert large_middleware._lc_helper.trigger == [
        ("fraction", 0.85),
        ("tokens", 24_000),
    ]
    assert large_middleware._lc_helper.keep == ("tokens", 8_000)
    assert large_middleware._truncate_args_trigger == ("tokens", 8_000)
    assert large_middleware._truncate_args_keep == ("messages", 1)
    assert large_middleware._max_arg_length == 1_200

    small = FakeListChatModel(responses=["ok"], profile={"max_input_tokens": 20_000})
    small_middleware = _summarization_middleware(small, FilesystemBackend(root_dir=tmp_path))
    assert small_middleware._lc_helper.trigger == [
        ("fraction", 0.85),
        ("tokens", 17_000),
    ]
    assert small_middleware._lc_helper.keep == ("tokens", 2_000)
    assert small_middleware._truncate_args_trigger == ("tokens", 8_000)


@pytest.mark.parametrize("profile", [None, {}])
def test_unprofiled_model_uses_native_trigger_or_early_token_trigger(tmp_path, profile) -> None:
    middleware = _summarization_middleware(
        FakeListChatModel(responses=["ok"], profile=profile),
        FilesystemBackend(root_dir=tmp_path),
    )
    assert middleware._lc_helper.trigger == [("tokens", 170_000), ("tokens", 24_000)]
