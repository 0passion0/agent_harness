import asyncio
from unittest.mock import Mock

import pytest
from langchain_core.exceptions import ContextOverflowError
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.types import Command, interrupt

from vertical_harness import HarnessSettings, RunContext
from vertical_harness.bridge.attachments import AttachmentContent
from vertical_harness.bridge.messages import DesktopMessages
from vertical_harness.bridge.runtime import DesktopRuntime
from vertical_harness.config.checkpoint_config import CheckpointConfig
from vertical_harness.core.compose import AgentComposer
from vertical_harness.langchain.streaming import AgentStreaming
from vertical_harness.middleware.context_limit import ContextLimitMiddleware
from vertical_harness.tools.user_interaction import UserInteraction
from vertical_harness.utils.usage import UsageEstimator


def options():
    return {
        "model_code": "deepseek:deepseek-chat",
        "runtimes": {"python": "python.exe", "node": "node.exe"},
        "permission_mode": "ask",
        "max_context_tokens": 128000,
        "agent_content": "instructions",
    }


@pytest.mark.parametrize(
    "choices,answer",
    [(None, "研究目标"), (["摘要", "完整报告"], "摘要"), (["摘要"], "自定义双语报告")],
)
def test_ask_user_interrupts_and_resumes_with_choice_or_custom_text(choices, answer):

    def step(_state):
        result = UserInteraction.ask_user.invoke({"question": "需要什么报告？", "options": choices})
        return {"messages": [AIMessage(content=result)]}

    graph = StateGraph(MessagesState)
    graph.add_node("question", step)
    graph.add_edge(START, "question")
    graph.add_edge("question", END)
    app = graph.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "question"}}
    paused = app.invoke({"messages": []}, config)
    pending = paused["__interrupt__"][0]
    assert pending.value == {
        "kind": "question",
        "question": "需要什么报告？",
        "options": choices or [],
    }
    resumed = app.invoke(Command(resume={pending.id: answer}), config)
    assert resumed["messages"][-1].content == answer


def test_ask_user_rejects_blank_questions_and_options():
    for params in ({"question": " "}, {"question": "format?", "options": [" "]}):
        with pytest.raises(ValueError, match="must not be blank"):
            UserInteraction.ask_user.invoke(params)


@pytest.mark.parametrize("mode", ["read_only", "ask", "full"])
def test_composed_desktop_agent_can_ask_questions_in_every_permission_mode(
    tmp_path, monkeypatch, mode
):
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

    from vertical_harness.core import compose

    class Model(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            assert any(getattr(item, "name", None) == "ask_user" for item in tools)
            return self

    monkeypatch.setattr(
        compose.ModelRouting,
        "init_configured_model",
        lambda _settings: Model(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": "ask",
                            "name": "ask_user",
                            "args": {"question": "选择格式", "options": ["摘要", "完整报告"]},
                        }
                    ],
                ),
                AIMessage(content="done"),
            ]
        ),
    )
    selected = {**options(), "permission_mode": mode}
    settings = HarnessSettings(
        _env_file=None,
        model="test:questions",
        workspace_root=tmp_path,
        store_backend="memory",
        enable_memory=False,
    )
    app = AgentComposer.build_agent(
        HarnessSettings.model_validate({**(settings).model_dump(), **selected}),
        checkpointer=InMemorySaver(),
    )

    async def run():
        config = {"configurable": {"thread_id": "questions"}}
        paused = await app.ainvoke({"messages": [HumanMessage(content="write report")]}, config)
        pending = paused["__interrupt__"][0]
        assert pending.value["options"] == ["摘要", "完整报告"]
        resumed = await app.ainvoke(Command(resume={pending.id: "自定义双语摘要"}), config)
        assert any(
            isinstance(message, ToolMessage) and message.content == "自定义双语摘要"
            for message in resumed["messages"]
        )

    asyncio.run(run())


def test_configured_settings_preserve_provider_model_and_runtime_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("VA_MODEL", "deepseek:configured")
    settings = DesktopRuntime.configured_settings(
        {
            "workspace_root": str(tmp_path / "workspace"),
            "runtime_data_dir": str(tmp_path / "runtime"),
            "options": options(),
        }
    )
    assert settings.model == "deepseek:deepseek-chat"
    assert settings.resolved_workspace_root == (tmp_path / "workspace").resolve()
    assert settings.python_executable == "python.exe"
    assert settings.node_executable == "node.exe"


@pytest.mark.parametrize("maximum", [0, 475001])
def test_configured_settings_rejects_context_outside_475k_budget(tmp_path, maximum):
    with pytest.raises(ValueError, match="between 1 and 475000"):
        DesktopRuntime.configured_settings(
            {
                "workspace_root": str(tmp_path / "workspace"),
                "runtime_data_dir": str(tmp_path / "runtime"),
                "options": {**options(), "max_context_tokens": maximum},
            }
        )


def test_attachments_inside_workspace_are_referenced_in_place(tmp_path):
    workspace = tmp_path / "workspace"
    original = workspace / "report.csv"
    original.parent.mkdir()
    original.write_text("a,b\n1,2\n", encoding="utf-8")
    message = DesktopMessages.attachment_message(
        workspace,
        "Analyze this",
        [{"name": "report.csv", "path": str(original), "media_type": "text/csv"}],
    )
    assert message[0]["text"] == "Analyze this"
    assert "Path: /report.csv" in message[1]["text"]


@pytest.mark.parametrize(
    "case,error,message",
    [
        ("not_dict", ValueError, "must contain"),
        ("extra_field", ValueError, "must contain"),
        ("non_string", ValueError, "must be strings"),
        ("relative", ValueError, "must be absolute"),
        ("missing", FileNotFoundError, None),
        ("directory", ValueError, "must refer to a file"),
        ("outside", ValueError, "must be inside the workspace"),
    ],
)
def test_all_attachments_are_validated_before_content_conversion(
    tmp_path, monkeypatch, case, error, message
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    image = workspace / "image.png"
    image.write_bytes(b"image")
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"outside")
    ref = {"name": image.name, "path": str(image), "media_type": "image/png"}
    invalid = {
        "not_dict": None,
        "extra_field": {**ref, "extra": "value"},
        "non_string": {**ref, "name": 1},
        "relative": {**ref, "path": image.name},
        "missing": {**ref, "path": str(workspace / "missing.png")},
        "directory": {**ref, "path": str(workspace)},
        "outside": {**ref, "path": str(outside)},
    }[case]
    convert = Mock()
    monkeypatch.setattr(AttachmentContent, "content_blocks", convert)
    with pytest.raises(error, match=message):
        DesktopMessages.attachment_message(workspace, "Analyze", [ref, invalid])
    convert.assert_not_called()


def test_snapshot_keeps_multimodal_content_and_marks_usage_estimate():
    message = AIMessage(
        content=[{"type": "text", "text": "done"}],
        usage_metadata={"input_tokens": 8, "output_tokens": 3, "total_tokens": 11},
    )
    dumped = DesktopMessages.raw_message(message)
    assert dumped["content"] == [{"type": "text", "text": "done"}]
    assert dumped["usage_metadata"]["total_tokens"] == 11
    assert UsageEstimator.usage_estimate([message], "deepseek:deepseek-chat")["used_tokens"] > 0


def test_context_usage_counts_actual_request_and_normalizes_reported_usage():
    messages = [
        SystemMessage(
            content=[
                {"type": "text", "text": "Real system instructions"},
                {"type": "text", "text": "## Skills System\nA skill catalog"},
                {"type": "text", "text": "User memory (untrusted reference data): preferences"},
            ]
        ),
        HumanMessage(content="Check this file"),
        AIMessage(
            content="",
            tool_calls=[
                {"id": "read", "name": "read_file", "args": {"file_path": "/skills/demo/SKILL.md"}}
            ],
        ),
        ToolMessage(content="Loaded skill instructions", tool_call_id="read"),
        AIMessage(content="Done"),
    ]
    report = UsageEstimator.usage_estimate(
        [],
        "test:model",
        100,
        request_messages=messages,
        tools=[{"name": "read_file", "description": "Read a file"}],
        reported_tokens=150,
    )
    sections = report["sections"].values()
    assert all(section["tokens"] > 0 for section in sections)
    assert sum(section["tokens"] for section in sections) == 150
    assert sum(section["ratio_of_used"] for section in sections) == pytest.approx(1)
    assert report["used_ratio"] == 1.5
    assert report["remaining_tokens"] == report["remaining_ratio"] == 0
    pending = UsageEstimator.usage_estimate([], "test:model", options=options())
    assert pending["used_tokens"] == 0
    assert pending["sections"]["tools"]["tokens"] is None


def test_attachment_rejects_relative_path(tmp_path):
    with pytest.raises(ValueError, match="absolute"):
        DesktopMessages.attachment_message(
            tmp_path, "text", [{"name": "file", "path": "relative.txt", "media_type": "text/plain"}]
        )


def test_context_limit_uses_the_routed_model_capacity():
    from langchain.agents.middleware import ModelRequest
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

    request = ModelRequest(
        model=FakeMessagesListChatModel(responses=[], profile={"max_input_tokens": 1000}),
        messages=[HumanMessage(content="history " * 2000)],
        tools=[],
    )
    middleware = ContextLimitMiddleware(475_000)
    with pytest.raises(ContextOverflowError, match=r"max_context_tokens \(1000\)"):
        middleware.wrap_model_call(
            request, lambda _request: pytest.fail("must not reach the model")
        )


def test_context_limit_rejects_oversized_history():
    from langchain.agents.middleware import ModelRequest
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

    middleware = ContextLimitMiddleware(1)
    request = ModelRequest(
        model=FakeMessagesListChatModel(responses=[]),
        messages=[HumanMessage(content="too many chars")],
        tools=[],
    )
    with pytest.raises(ContextOverflowError, match="exceeds configured max_context_tokens"):
        middleware.wrap_model_call(request, lambda _request: None)

    async def reject_async_request():
        with pytest.raises(ContextOverflowError, match="exceeds configured max_context_tokens"):
            await middleware.awrap_model_call(request, lambda _request: None)

    asyncio.run(reject_async_request())


@pytest.mark.parametrize("use_async", [False, True])
@pytest.mark.parametrize("section", ["system", "tools"])
def test_context_limit_counts_actual_system_prompt_and_tool_schemas(use_async, section):
    from langchain.agents.middleware import ModelRequest
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

    request = ModelRequest(
        model=FakeMessagesListChatModel(responses=[]),
        messages=[],
        system_message=SystemMessage(content="instructions " * 1000)
        if section == "system"
        else None,
        tools=[
            {
                "name": "inspect",
                "description": "Inspect data",
                "parameters": {
                    "type": "object",
                    "properties": {"input": {"type": "string", "description": "schema " * 1000}},
                },
            }
        ]
        if section == "tools"
        else [],
    )
    middleware = ContextLimitMiddleware(100)

    def handler(_request):
        pytest.fail("An oversized request must not reach the model")

    with pytest.raises(ContextOverflowError, match="exceeds configured max_context_tokens"):
        if use_async:
            asyncio.run(middleware.awrap_model_call(request, handler))
        else:
            middleware.wrap_model_call(request, handler)


@pytest.mark.parametrize("use_async", [False, True])
@pytest.mark.parametrize("history_chars", [20000, 8000])
def test_desktop_context_limit_checks_compacted_model_request(
    tmp_path, monkeypatch, use_async, history_chars
):
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from pydantic import Field

    from vertical_harness.core import compose

    class TrackingModel(FakeMessagesListChatModel):
        seen_messages: list = Field(default_factory=list)

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            self.seen_messages.append(messages)
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    model = TrackingModel(
        responses=[
            AIMessage(content="History summary."),
            AIMessage(content="Done."),
            AIMessage(content="Follow-up done."),
        ],
        profile={"max_input_tokens": 20000},
    )
    monkeypatch.setattr(compose.ModelRouting, "init_configured_model", lambda _settings: model)
    selected = {**options(), "permission_mode": "full", "max_context_tokens": 12000}
    selected["agent_content"] = ""
    selected["skills"] = []
    settings = HarnessSettings(
        _env_file=None,
        model="test:context-limit",
        workspace_root=tmp_path / "workspace",
        runtime_data_dir=tmp_path / "runtime",
        store_backend="memory",
        enable_memory=False,
    )
    history = [
        message
        for index in range(6)
        for message in (
            HumanMessage(content=f"Historical request {index}: " + "x" * history_chars),
            AIMessage(content=f"Verified result {index} was recorded."),
        )
    ]
    config = {"configurable": {"thread_id": f"context-limit-{use_async}"}}

    async def scenario():
        async with AgentComposer.aopen_agent(
            HarnessSettings.model_validate({**(settings).model_dump(), **selected})
        ) as app:
            if use_async:
                result = await app.ainvoke({"messages": history}, config)
                snapshot = await app.aget_state(config)
            else:
                result = app.invoke({"messages": history}, config)
                snapshot = app.get_state(config)
            assert result["messages"][-1].content == "Done."
            assert any(
                "Historical request 0" in str(message) for message in snapshot.values["messages"]
            )
            assert "Historical request 0" not in repr(model.seen_messages[-1])
            follow_up = {"messages": [HumanMessage(content="Continue from the verified result.")]}
            if use_async:
                continued = await app.ainvoke(follow_up, config)
            else:
                continued = app.invoke(follow_up, config)
            assert continued["messages"][-1].content == "Follow-up done."
            assert len(model.seen_messages) == 3
            assert "Historical request 0" not in repr(model.seen_messages[-1])

    asyncio.run(scenario())


def test_read_only_real_graph_blocks_builtin_write_file(tmp_path, monkeypatch):
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

    from vertical_harness.core import compose

    class ToolCallingFakeModel(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    monkeypatch.setattr(
        compose.ModelRouting,
        "init_configured_model",
        lambda _settings: ToolCallingFakeModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "write_file",
                            "args": {"file_path": "blocked.txt", "content": "x"},
                            "id": "write-call",
                            "type": "tool_call",
                        }
                    ],
                )
            ]
        ),
    )
    selected = options()
    selected["permission_mode"] = "read_only"
    settings = HarnessSettings.model_validate(
        {
            "model": "deepseek:deepseek-chat",
            "workspace_root": tmp_path / "workspace",
            "runtime_data_dir": tmp_path / "runtime",
        }
    )

    async def invoke():
        async with AgentComposer.aopen_agent(
            HarnessSettings.model_validate({**(settings).model_dump(), **selected})
        ) as app:
            assert app.config["recursion_limit"] == 9999
            await app.ainvoke(
                {"messages": [HumanMessage(content="write blocked.txt")]},
                {"configurable": {"thread_id": "readonly-check"}},
            )

    with pytest.raises(PermissionError, match="disabled in read_only"):
        asyncio.run(invoke())
    assert not (tmp_path / "blocked.txt").exists()


def test_ask_rejection_returns_tool_error_and_allows_model_to_continue(tmp_path, monkeypatch):
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

    from vertical_harness.core import compose

    class ToolCallingFakeModel(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    monkeypatch.setattr(
        compose.ModelRouting,
        "init_configured_model",
        lambda _settings: ToolCallingFakeModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "write_file",
                            "args": {"file_path": "rejected.txt", "content": "x"},
                            "id": "write-call",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="I will leave that file unchanged."),
            ]
        ),
    )
    selected = options()
    selected["permission_mode"] = "ask"
    settings = HarnessSettings.model_validate(
        {
            "model": "deepseek:deepseek-chat",
            "workspace_root": tmp_path / "workspace",
            "runtime_data_dir": tmp_path / "runtime",
        }
    )

    async def scenario():
        async with AgentComposer.aopen_agent(
            HarnessSettings.model_validate({**(settings).model_dump(), **selected})
        ) as app:
            config = {"configurable": {"thread_id": "ask-reject-check"}}
            await app.ainvoke({"messages": [HumanMessage(content="write rejected.txt")]}, config)
            paused = await app.aget_state(config)
            pending = paused.tasks[0].interrupts[0]
            result = await app.ainvoke(
                Command(resume={pending.id: {"decisions": [{"type": "reject"}]}}), config
            )
            assert result["messages"][-2].status == "error"
            assert result["messages"][-2].tool_call_id == "write-call"
            assert result["messages"][-1].content == "I will leave that file unchanged."

    asyncio.run(scenario())
    assert not (tmp_path / "workspace" / "rejected.txt").exists()


def test_streaming_six_tool_calls_keeps_agent_recursion_limit(tmp_path, monkeypatch):
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

    from vertical_harness.core import compose

    class ToolCallingFakeModel(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    responses = [
        AIMessage(
            content="",
            tool_calls=[
                {"name": "ls", "args": {"path": "."}, "id": f"ls-call-{index}", "type": "tool_call"}
            ],
        )
        for index in range(6)
    ]
    responses.append(AIMessage(content="Finished after six listings."))
    monkeypatch.setattr(
        compose.ModelRouting,
        "init_configured_model",
        lambda _settings: ToolCallingFakeModel(responses=responses),
    )
    selected = options()
    selected["permission_mode"] = "full"
    settings = HarnessSettings.model_validate(
        {
            "model": "deepseek:deepseek-chat",
            "workspace_root": tmp_path / "workspace",
            "runtime_data_dir": tmp_path / "runtime",
        }
    )

    async def scenario():
        async with AgentComposer.aopen_agent(
            HarnessSettings.model_validate({**(settings).model_dump(), **selected})
        ) as app:
            assert app.config["recursion_limit"] == 9999
            config = {
                **CheckpointConfig.make_checkpoint_config("six-tool-call-check"),
                "run_id": "six-tool-run",
                "recursion_limit": app.config["recursion_limit"],
            }
            events = [
                event
                async for event in AgentStreaming.astream_agent(
                    app,
                    {
                        "messages": [
                            HumanMessage(content="List this directory six times, then stop.")
                        ]
                    },
                    config=config,
                    context=RunContext(tenant_id="test", user_id="test"),
                )
            ]
            assert sum(event.type == "tool.completed" for event in events) == 6
            assert any(event.type == "run.completed" for event in events)
            assert not any(event.type == "run.failed" for event in events)

    asyncio.run(scenario())


def test_clearing_durable_interrupt_allows_new_message_with_pending_tool_call():

    async def scenario():
        graph = StateGraph(MessagesState)

        def plan(state):
            if state["messages"][-1].content == "first task":
                return {
                    "messages": [
                        AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "name": "write_file",
                                    "args": {"file_path": "note.txt", "content": "x"},
                                    "id": "call-1",
                                    "type": "tool_call",
                                }
                            ],
                        )
                    ]
                }
            return {"messages": [AIMessage(content="second task completed")]}

        def action(_state):
            interrupt({"action_requests": [{"name": "write_file"}], "kind": "approval"})
            return {"messages": [ToolMessage(content="written", tool_call_id="call-1")]}

        graph.add_node("plan", plan)
        graph.add_node("action", action)
        graph.add_edge(START, "plan")
        graph.add_conditional_edges(
            "plan", lambda state: "action" if state["messages"][-1].tool_calls else END
        )
        graph.add_edge("action", END)
        app = graph.compile(checkpointer=InMemorySaver())
        config = {"configurable": {"thread_id": "stop-resume-check"}}
        await app.ainvoke({"messages": [HumanMessage(content="first task")]}, config)
        interrupted = await app.aget_state(config)
        assert interrupted.tasks[0].interrupts
        assert interrupted.values["messages"][-1].tool_calls
        await app.aupdate_state(config, interrupted.values, as_node=interrupted.next[0])
        resumed = await app.ainvoke({"messages": [HumanMessage(content="second task")]}, config)
        assert resumed["messages"][-1].content == "second task completed"

    asyncio.run(scenario())
