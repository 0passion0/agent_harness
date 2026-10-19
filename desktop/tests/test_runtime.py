from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command, interrupt
from runtime import (
    ContextLimitMiddleware,
    DesktopPermissionMiddleware,
    DesktopPlugin,
    aopen_desktop_agent,
    attachment_message,
    configured_settings,
    raw_message,
    usage_estimate,
)

from vertical_harness import (
    BuildServices,
    HarnessSettings,
    RunContext,
    astream_agent,
    build_agent,
    make_checkpoint_config,
)


def options():
    return {
        "model_code": "deepseek:deepseek-chat", "exa_api_key": "secret",
        "runtimes": {"python": "python.exe", "node": "node.exe"},
        "permission_mode": "ask", "max_context_tokens": 4096, "agent_content": "instructions",
        "skills": [{"id": "demo", "content": "skill text"}], "mcp_servers": {},
        "analysis_enabled": False,
        "exa_enabled": False,
        "analysis_plugin_dir": str(Path(__file__).parents[1] / "resources" / "plugin_sources" / "data_analysis"),
    }


def test_configured_settings_preserve_provider_model_and_runtime_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("VA_MODEL", "deepseek:configured")
    settings, desktop = configured_settings({
        "workspace_root": str(tmp_path / "workspace"),
        "runtime_data_dir": str(tmp_path / "runtime"), "options": options(),
    })
    assert settings.model == "deepseek:deepseek-chat"
    assert settings.resolved_workspace_root == (tmp_path / "workspace").resolve()
    assert desktop["runtimes"] == {"python": "python.exe", "node": "node.exe"}


def test_attachments_inside_workspace_are_referenced_in_place(tmp_path):
    workspace = tmp_path / "workspace"
    original = workspace / "report.csv"
    original.parent.mkdir()
    original.write_text("a,b\n1,2\n", encoding="utf-8")
    message = attachment_message(workspace, "Analyze this", [{
        "name": "report.csv", "path": str(original), "media_type": "text/csv",
    }])
    assert message[0]["text"] == "Analyze this"
    assert "Path: /report.csv" in message[1]["text"]


def test_snapshot_keeps_multimodal_content_and_marks_usage_estimate():
    message = AIMessage(content=[{"type": "text", "text": "done"}], usage_metadata={
        "input_tokens": 8, "output_tokens": 3, "total_tokens": 11,
    })
    dumped = raw_message(message)
    assert dumped["content"] == [{"type": "text", "text": "done"}]
    assert dumped["usage_metadata"]["total_tokens"] == 11
    assert usage_estimate([message], "deepseek:deepseek-chat")["used_tokens"] > 0


def test_attachment_rejects_relative_path(tmp_path):
    with pytest.raises(ValueError, match="absolute"):
        attachment_message(tmp_path, "text", [{
            "name": "file", "path": "relative.txt", "media_type": "text/plain",
        }])


def test_read_only_mode_blocks_builtin_write_tools(tmp_path):
    selected = options()
    selected["permission_mode"] = "read_only"
    contribution = DesktopPlugin(selected).build(BuildServices(tmp_path))
    assert contribution.tools == []
    permission = next(item for item in contribution.middleware if isinstance(item, DesktopPermissionMiddleware))
    from types import SimpleNamespace
    with pytest.raises(PermissionError, match="disabled in read_only"):
        permission.wrap_tool_call(SimpleNamespace(tool=SimpleNamespace(name="write_file")), lambda request: request)


def test_context_limit_rejects_oversized_history():
    middleware = ContextLimitMiddleware(1, "", [])
    with pytest.raises(ValueError, match="exceeds configured max_context_tokens"):
        middleware.before_model({"messages": [HumanMessage(content="too many chars")]}, None)


@pytest.mark.parametrize("mode", ["read_only", "ask", "full"])
def test_build_agent_composes_each_desktop_permission_mode(tmp_path, mode):
    selected = options()
    selected["permission_mode"] = mode
    settings = HarnessSettings.model_validate({
        "model": "deepseek:deepseek-chat", "workspace_root": tmp_path,
        "runtime_data_dir": tmp_path, "enabled_plugins": [],
    })
    agent = build_agent(settings, store=InMemoryStore(), checkpointer=InMemorySaver(),
                        mcp_tools=[], plugins=[DesktopPlugin(selected)])
    assert agent.get_graph().nodes


def test_read_only_real_graph_blocks_builtin_write_file(tmp_path, monkeypatch):
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

    from vertical_harness.engine import compose

    class ToolCallingFakeModel(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    monkeypatch.setattr(compose, "init_configured_model", lambda _settings: ToolCallingFakeModel(
        responses=[AIMessage(content="", tool_calls=[{
            "name": "write_file", "args": {"file_path": "blocked.txt", "content": "x"},
            "id": "write-call", "type": "tool_call",
        }])]
    ))
    selected = options()
    selected["permission_mode"] = "read_only"
    settings = HarnessSettings.model_validate({
        "model": "deepseek:deepseek-chat", "workspace_root": tmp_path / "workspace",
        "runtime_data_dir": tmp_path / "runtime", "enabled_plugins": [],
    })

    async def invoke():
        async with aopen_desktop_agent(settings, selected) as app:
            assert app.config["recursion_limit"] == 9_999
            await app.ainvoke(
                {"messages": [HumanMessage(content="write blocked.txt")]},
                {"configurable": {"thread_id": "readonly-check"}},
            )

    with pytest.raises(PermissionError, match="disabled in read_only"):
        asyncio.run(invoke())
    assert not (tmp_path / "blocked.txt").exists()


def test_ask_rejection_returns_tool_error_and_allows_model_to_continue(tmp_path, monkeypatch):
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

    from vertical_harness.engine import compose

    class ToolCallingFakeModel(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    monkeypatch.setattr(compose, "init_configured_model", lambda _settings: ToolCallingFakeModel(
        responses=[
            AIMessage(content="", tool_calls=[{
                "name": "write_file", "args": {"file_path": "rejected.txt", "content": "x"},
                "id": "write-call", "type": "tool_call",
            }]),
            AIMessage(content="I will leave that file unchanged."),
        ]
    ))
    selected = options()
    selected["permission_mode"] = "ask"
    settings = HarnessSettings.model_validate({
        "model": "deepseek:deepseek-chat", "workspace_root": tmp_path / "workspace",
        "runtime_data_dir": tmp_path / "runtime", "enabled_plugins": [],
    })

    async def scenario():
        async with aopen_desktop_agent(settings, selected) as app:
            config = {"configurable": {"thread_id": "ask-reject-check"}}
            await app.ainvoke({"messages": [HumanMessage(content="write rejected.txt")]}, config)
            paused = await app.aget_state(config)
            pending = paused.tasks[0].interrupts[0]
            result = await app.ainvoke(
                Command(resume={pending.id: {"decisions": [{"type": "reject"}]}}), config,
            )
            assert result["messages"][-2].status == "error"
            assert result["messages"][-2].tool_call_id == "write-call"
            assert result["messages"][-1].content == "I will leave that file unchanged."

    asyncio.run(scenario())
    assert not (tmp_path / "workspace" / "rejected.txt").exists()


def test_streaming_six_tool_calls_keeps_agent_recursion_limit(tmp_path, monkeypatch):
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

    from vertical_harness.engine import compose

    class ToolCallingFakeModel(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    responses = [
        AIMessage(content="", tool_calls=[{
            "name": "ls", "args": {"path": "."},
            "id": f"ls-call-{index}", "type": "tool_call",
        }])
        for index in range(6)
    ]
    responses.append(AIMessage(content="Finished after six listings."))
    monkeypatch.setattr(compose, "init_configured_model", lambda _settings: ToolCallingFakeModel(
        responses=responses
    ))
    selected = options()
    selected["permission_mode"] = "full"
    settings = HarnessSettings.model_validate({
        "model": "deepseek:deepseek-chat", "workspace_root": tmp_path / "workspace",
        "runtime_data_dir": tmp_path / "runtime", "enabled_plugins": [],
    })

    async def scenario():
        async with aopen_desktop_agent(settings, selected) as app:
            assert app.config["recursion_limit"] == 9_999
            config = {
                **make_checkpoint_config("six-tool-call-check"),
                "run_id": "six-tool-run",
                "recursion_limit": app.config["recursion_limit"],
            }
            events = [event async for event in astream_agent(
                app,
                {"messages": [HumanMessage(content="List this directory six times, then stop.")]},
                config=config,
                context=RunContext(tenant_id="test", user_id="test"),
            )]
            assert sum(event.type == "tool.completed" for event in events) == 6
            assert any(event.type == "run.completed" for event in events)
            assert not any(event.type == "run.failed" for event in events)

    asyncio.run(scenario())


def test_clearing_durable_interrupt_allows_new_message_with_pending_tool_call():
    async def scenario():
        graph = StateGraph(MessagesState)

        def plan(state):
            if state["messages"][-1].content == "first task":
                return {"messages": [AIMessage(content="", tool_calls=[{
                    "name": "write_file", "args": {"file_path": "note.txt", "content": "x"},
                    "id": "call-1", "type": "tool_call",
                }])]}
            return {"messages": [AIMessage(content="second task completed")]}

        def action(_state):
            interrupt({"action_requests": [{"name": "write_file"}], "kind": "approval"})
            return {"messages": [ToolMessage(content="written", tool_call_id="call-1")]}

        graph.add_node("plan", plan)
        graph.add_node("action", action)
        graph.add_edge(START, "plan")
        graph.add_conditional_edges("plan", lambda state: "action" if state["messages"][-1].tool_calls else END)
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
