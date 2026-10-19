from __future__ import annotations

import asyncio
import os
import subprocess
from contextlib import asynccontextmanager
from io import StringIO
from unittest.mock import Mock
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph

from vertical_harness import aopen_checkpointer
from vertical_harness import desktop as bridge

APPROVAL = {"decisions": [{"type": "approve"}]}


@pytest.mark.parametrize("result,expected", [
    (subprocess.CompletedProcess([], 0, stdout="revision-dirty\n"), "revision-dirty"),
    (subprocess.CompletedProcess([], 0, stdout=""), "unknown"),
    (subprocess.TimeoutExpired("git", 2), "unknown"),
    (subprocess.CalledProcessError(128, "git"), "unknown"),
    (FileNotFoundError("git"), "unknown"),
])
def test_stdio_startup_resolves_trace_revision_without_inheriting_protocol_input(monkeypatch, result, expected):
    from langsmith.env import _runtime_env

    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.delenv("LANGSMITH_REVISION_ID", raising=False)
    monkeypatch.delenv("LANGCHAIN_REVISION_ID", raising=False)
    monkeypatch.setattr(bridge.sys, "stdin", StringIO(""))
    probe = Mock(side_effect=result) if isinstance(result, Exception) else Mock(return_value=result)
    monkeypatch.setattr(subprocess, "run", probe)
    sdk_probe = Mock(return_value=None)
    monkeypatch.setattr(_runtime_env, "exec_git", sdk_probe)

    asyncio.run(bridge.main())

    probe.assert_called_once_with(
        ["git", "describe", "--tags", "--always", "--dirty"],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, check=True, timeout=2,
    )
    assert os.environ["LANGSMITH_REVISION_ID"] == expected
    assert _runtime_env.get_langchain_env_var_metadata.__wrapped__()["revision_id"] == expected
    sdk_probe.assert_not_called()


@pytest.mark.parametrize("tracing,revision_key", [
    ("false", None), ("true", "LANGSMITH_REVISION_ID"), ("true", "LANGCHAIN_REVISION_ID"),
])
def test_stdio_startup_skips_revision_probe_when_disabled_or_explicit(monkeypatch, tracing, revision_key):
    monkeypatch.setenv("LANGSMITH_TRACING", tracing)
    monkeypatch.delenv("LANGSMITH_REVISION_ID", raising=False)
    monkeypatch.delenv("LANGCHAIN_REVISION_ID", raising=False)
    if revision_key:
        monkeypatch.setenv(revision_key, "configured-revision")
    monkeypatch.setattr(bridge.sys, "stdin", StringIO(""))
    probe = Mock()
    monkeypatch.setattr(subprocess, "run", probe)

    asyncio.run(bridge.main())

    probe.assert_not_called()
    if revision_key:
        assert os.environ[revision_key] == "configured-revision"


def agent(checkpointer=None, body_calls=None):
    def step(_state):
        from langgraph.types import interrupt

        decision = interrupt(
            {
                "action_requests": [
                    {
                        "name": "write_note",
                        "args": {"text": "private note"},
                    }
                ]
            }
        )
        if body_calls is not None:
            body_calls.append(decision)
        return {"messages": [AIMessage(content="approved")]}

    graph = StateGraph(MessagesState)
    graph.add_node("step", step)
    graph.add_edge(START, "step")
    graph.add_edge("step", END)
    return graph.compile(checkpointer=checkpointer or InMemorySaver()).with_config(
        {"recursion_limit": 9999}
    )


async def configure(monkeypatch, tmp_path, enabled_plugins=(), authorized_plugins=()):
    monkeypatch.setenv("VA_MODEL", "test:fake")
    return await bridge.loop.dispatch(
        "configure",
        {
            "tenant_id": "tenant-a",
            "user_id": "user-a",
            "workspace_root": str(tmp_path / "workspace"),
            "runtime_data_dir": str(tmp_path / "runtime"),
            "options": {
                "model_code": "",
                "runtimes": {"python": "", "node": ""},
                "permission_mode": "ask",
                "max_context_tokens": 128000,
                "agent_content": "",
                "skills": [],
                "mcp_servers": {},
                "enabled_plugins": list(enabled_plugins),
                "authorized_plugins": list(authorized_plugins),
            },
        },
    )


def test_configure_restores_only_host_supplied_plugin_grants(monkeypatch, tmp_path):
    async def scenario():
        await configure(monkeypatch, tmp_path, ["demo.plugin"], ["demo.plugin"])
        gateway = bridge.loop.auth_gateway
        gateway.verify_execution(
            plugin_id="demo.plugin", workflow_name="workflow", user_id="user-a",
            tenant_id="tenant-a", thread_id="new-chat", run_id="new-run",
        )
        await configure(monkeypatch, tmp_path, ["demo.plugin"])
        assert not bridge.loop.auth_gateway.is_authorized(
            "demo.plugin", user_id="user-a", tenant_id="tenant-a",
        )

    asyncio.run(scenario())


def test_configure_isolates_store_and_checkpoint_paths(monkeypatch, tmp_path):
    result = asyncio.run(configure(monkeypatch, tmp_path))
    assert result["model"] == "test:fake"
    assert "environment" in result
    assert bridge.loop.settings.store_db_path == tmp_path / "runtime" / "store.sqlite"
    assert bridge.loop.settings.checkpoint_db_path == tmp_path / "runtime" / "checkpoints.sqlite"
    assert bridge.loop.settings.resolved_workspace_root == (tmp_path / "workspace").resolve()


def test_snapshot_of_new_sqlite_thread_has_no_run(monkeypatch, tmp_path):
    async def scenario():
        await configure(monkeypatch, tmp_path)
        thread_id = str(uuid4())

        @asynccontextmanager
        async def open_agent(_state):
            async with aopen_checkpointer(bridge.loop.settings) as saver:
                yield agent(saver)

        monkeypatch.setattr(bridge, "open_configured_agent", open_agent)
        snapshot = await bridge.loop.dispatch("snapshot", {"thread_id": thread_id})
        assert snapshot["thread_id"] == thread_id
        assert snapshot["run_id"] is None
        assert snapshot["messages"] == []
        assert snapshot["interrupts"] == []
        assert snapshot["artifacts"] == []
        assert snapshot["token_usage"]["max_tokens"] == 128000

    asyncio.run(scenario())


def test_actual_model_context_is_streamed_persisted_and_isolated_by_chat(monkeypatch, tmp_path):
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

    from vertical_harness.engine import compose

    class Model(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    monkeypatch.setattr(compose, "init_configured_model", lambda settings: Model(responses=[
        AIMessage(content="Done", usage_metadata={"input_tokens": 800, "output_tokens": 20, "total_tokens": 820})
    ]))
    frames = []

    async def capture(state, method, params):
        frames.append(params)

    monkeypatch.setattr(bridge, "notify", capture)

    async def scenario():
        await configure(monkeypatch, tmp_path)
        bridge.loop.settings.enable_memory = False
        thread_id = str(uuid4())
        run_id = str(uuid4())
        await bridge.loop.dispatch("run.start", {"thread_id": thread_id, "run_id": run_id, "text": "Hello", "selected_plugins": [], "selected_skills": []})
        task = bridge.loop.active[run_id][1]
        await task
        completed = next(frame for frame in frames if frame["type"] == "model.completed")
        assert completed["data"]["token_usage"]["used_tokens"] == 820
        report = await bridge.loop.context_usage(thread_id, [])
        assert report["used_tokens"] == 820
        assert report["sections"]["system_prompt"]["tokens"] > 0
        assert (await bridge.loop.context_usage(str(uuid4()), []))["used_tokens"] == 0
        bridge.loop.desktop_options["max_context_tokens"] = 400
        resized = await bridge.loop.context_usage(thread_id, [])
        assert resized["used_ratio"] == 2.05
        assert resized["remaining_tokens"] == 0
        await bridge.loop.dispatch("session.delete", {"thread_id": thread_id})
        assert (await bridge.loop.context_usage(thread_id, []))["used_tokens"] == 0

    asyncio.run(scenario())


def test_snapshot_reopens_sqlite_checkpoint_with_messages_and_interrupt(monkeypatch, tmp_path):
    async def scenario():
        await configure(monkeypatch, tmp_path)
        thread_id = str(uuid4())
        run_id = str(uuid4())
        async with aopen_checkpointer(bridge.loop.settings) as saver:
            graph = agent(saver)
            await graph.ainvoke(
                {"messages": [HumanMessage(content="hello")]},
                {
                    "configurable": {"thread_id": thread_id},
                    "metadata": {"desktop_run_id": run_id},
                },
            )

        @asynccontextmanager
        async def open_agent(_state):
            async with aopen_checkpointer(bridge.loop.settings) as saver:
                yield agent(saver)

        monkeypatch.setattr(bridge, "open_configured_agent", open_agent)
        snapshot = await bridge.loop.dispatch("snapshot", {"thread_id": thread_id})
        assert snapshot["run_id"] == run_id
        assert snapshot["messages"][0]["data"]["content"] == "hello"
        assert snapshot["messages"][0]["message"]["content"] == "hello"
        assert snapshot["interrupts"][0]["value"] == {
            "action_requests": [{"name": "write_note", "args": {"text": "private note"}}],
        }
        assert snapshot["interrupts"][0]["id"]
        assert "private note" not in str(snapshot["messages"])

    asyncio.run(scenario())


def test_stream_resumes_by_real_interrupt_id_and_cleans_up_before_terminal(monkeypatch, tmp_path):
    async def scenario():
        body_calls = []
        graph = agent(body_calls=body_calls)
        await configure(monkeypatch, tmp_path)
        context_exited = False

        @asynccontextmanager
        async def open_agent(_state):
            nonlocal context_exited
            try:
                yield graph
            finally:
                context_exited = True

        monkeypatch.setattr(bridge, "open_configured_agent", open_agent)
        events = []

        async def capture(_state, method, params):
            if method == "agent.event" and params["type"] in {
                "run.completed",
                "run.interrupted",
                "run.failed",
            }:
                assert params["run_id"] not in bridge.loop.active
                assert context_exited
            events.append((method, params))

        monkeypatch.setattr(bridge, "notify", capture)

        thread_id, run_id = str(uuid4()), str(uuid4())
        await bridge.loop.dispatch(
            "run.start",
            {
                "thread_id": thread_id,
                "run_id": run_id,
                "text": "hello",
                "selected_plugins": [],
                "selected_skills": [],
            },
        )
        await bridge.loop.active[run_id][1]
        interrupted = next(
            p for m, p in events if m == "agent.event" and p["type"] == "run.interrupted"
        )
        interrupt_id = interrupted["data"]["interrupts"][0]["id"]

        await bridge.loop.dispatch(
            "run.resume",
            {
                "thread_id": thread_id,
                "run_id": run_id,
                "decisions": {interrupt_id: APPROVAL},
            },
        )
        await bridge.loop.active[run_id][1]
        assert body_calls == [APPROVAL]
        assert any(m == "agent.event" and p["type"] == "run.completed" for m, p in events)

    asyncio.run(scenario())


def test_plugin_authorization_resume_accepts_exact_decision_contract(monkeypatch, tmp_path):
    async def scenario():
        await configure(monkeypatch, tmp_path)
        events = []
        body_calls = []
        saver = InMemorySaver()

        def graph():
            def step(_state):
                from langgraph.types import interrupt

                decision = interrupt(
                    {
                        "kind": "plugin_authorization",
                        "request_id": "req-current-run",
                        "plugin_id": "demo.plugin",
                        "workflow_name": "demo_workflow",
                        "purpose": "test",
                        "input_scope": [],
                        "planned_outputs": [],
                        "allowed_effects": ["read"],
                        "question": "Allow this workflow?",
                    }
                )
                body_calls.append(decision)
                return {"messages": [AIMessage(content="approved")]}

            state = StateGraph(MessagesState)
            state.add_node("step", step)
            state.add_edge(START, "step")
            state.add_edge("step", END)
            return state.compile(checkpointer=saver).with_config({"recursion_limit": 9999})

        @asynccontextmanager
        async def open_agent(_state):
            yield graph()

        monkeypatch.setattr(bridge, "open_configured_agent", open_agent)

        async def capture(_state, method, params):
            events.append((method, params))

        monkeypatch.setattr(bridge, "notify", capture)
        thread_id, run_id = str(uuid4()), str(uuid4())
        await bridge.loop.dispatch(
            "run.start",
            {
                "thread_id": thread_id,
                "run_id": run_id,
                "text": "run plugin",
                "selected_plugins": [],
                "selected_skills": [],
            },
        )
        await bridge.loop.active[run_id][1]
        interrupted = next(
            p for m, p in events if m == "agent.event" and p["type"] == "run.interrupted"
        )
        interrupt_id = interrupted["data"]["interrupts"][0]["id"]
        decision = {"request_id": "req-current-run", "approved": True}
        await bridge.loop.dispatch(
            "run.resume",
            {
                "thread_id": thread_id,
                "run_id": run_id,
                "decisions": {interrupt_id: decision},
            },
        )
        await bridge.loop.active[run_id][1]
        assert body_calls == [decision]
        assert any(m == "agent.event" and p["type"] == "run.completed" for m, p in events)

    asyncio.run(scenario())


def test_plugin_approval_survives_checkpoint_reconfigure_and_run_completion(
    monkeypatch, tmp_path
):
    async def scenario():
        await configure(monkeypatch, tmp_path, ["demo.plugin"])
        saver = InMemorySaver()

        def graph():
            def step(_state):
                from langgraph.types import interrupt

                interrupt(
                    {
                        "kind": "plugin_authorization",
                        "request_id": "req-scoped",
                        "plugin_id": "demo.plugin",
                        "workflow_name": "demo_workflow",
                        "purpose": "read the approved file",
                        "input_scope": ["source.csv"],
                        "planned_outputs": [],
                        "allowed_effects": ["read"],
                        "question": "Allow this workflow?",
                    }
                )
                interrupt({"kind": "confirmation", "question": "Continue?"})
                return {"messages": [AIMessage(content="done")]}

            state = StateGraph(MessagesState)
            state.add_node("step", step)
            state.add_edge(START, "step")
            state.add_edge("step", END)
            return state.compile(checkpointer=saver).with_config({"recursion_limit": 9999})

        @asynccontextmanager
        async def open_agent(_state):
            yield graph()

        monkeypatch.setattr(bridge, "open_configured_agent", open_agent)
        monkeypatch.setattr(bridge, "notify", lambda *_args: asyncio.sleep(0))
        thread_id, run_id = str(uuid4()), str(uuid4())
        await bridge.loop.dispatch(
            "run.start",
            {
                "thread_id": thread_id,
                "run_id": run_id,
                "text": "read source",
                "selected_plugins": [],
                "selected_skills": [],
            },
        )
        await bridge.loop.active[run_id][1]
        first_state = await graph().aget_state({"configurable": {"thread_id": thread_id}})
        plugin_interrupt = first_state.tasks[0].interrupts[0]
        await bridge.loop.dispatch(
            "run.resume",
            {
                "thread_id": thread_id,
                "run_id": run_id,
                "decisions": {
                    plugin_interrupt.id: {"request_id": "req-scoped", "approved": True}
                },
            },
        )
        assert bridge.loop.plugin_approvals
        await bridge.loop.active[run_id][1]
        paused_state = await graph().aget_state({"configurable": {"thread_id": thread_id}})
        confirmation = paused_state.tasks[0].interrupts[0]
        saved_approvals = await bridge.loop._load_plugin_approvals(thread_id, run_id)
        assert saved_approvals[0]["input_scope"] == ["source.csv"]

        await configure(monkeypatch, tmp_path, ["demo.plugin"])
        await bridge.loop.dispatch(
            "run.resume",
            {
                "thread_id": thread_id,
                "run_id": run_id,
                "decisions": {confirmation.id: "continue"},
            },
        )
        gateway = bridge.loop.auth_gateway
        gateway.verify_execution(
            plugin_id="demo.plugin",
            workflow_name="demo_workflow",
            user_id="user-a",
            tenant_id="tenant-a",
            thread_id=thread_id,
            run_id=run_id,
            input_data={"file_path": "source.csv"},
        )
        gateway.verify_execution(
            plugin_id="demo.plugin",
            workflow_name="demo_workflow",
            user_id="user-a",
            tenant_id="tenant-a",
            thread_id=thread_id,
            run_id=run_id,
            input_data={"file_path": "other.csv"},
        )
        await bridge.loop.active[run_id][1]
        assert await bridge.loop._load_plugin_approvals(thread_id, run_id) == []
        assert gateway.is_authorized(
            "demo_workflow", thread_id=thread_id, user_id="user-a", tenant_id="tenant-a"
        )

    asyncio.run(scenario())


def test_stop_cancels_active_run(monkeypatch, tmp_path):
    async def scenario():
        await configure(monkeypatch, tmp_path)
        entered = asyncio.Event()

        @asynccontextmanager
        async def slow_open(_state):
            entered.set()
            await asyncio.Event().wait()
            yield None

        monkeypatch.setattr(bridge, "open_configured_agent", slow_open)
        events = []

        async def capture(_state, method, params):
            events.append((method, params))

        monkeypatch.setattr(bridge, "notify", capture)
        thread_id, run_id = str(uuid4()), str(uuid4())
        await bridge.loop.dispatch(
            "run.start",
            {
                "thread_id": thread_id,
                "run_id": run_id,
                "text": "cancel",
                "selected_plugins": [],
                "selected_skills": [],
            },
        )
        await entered.wait()
        assert (
            await bridge.loop.dispatch("run.stop", {"run_id": run_id, "thread_id": thread_id}) == {}
        )
        assert events == [("run.cancelled", {"run_id": run_id, "thread_id": thread_id})]

    asyncio.run(scenario())


def test_stop_paused_run_checks_identity_and_clears_current_run(monkeypatch, tmp_path):
    async def scenario():
        await configure(monkeypatch, tmp_path)
        saver = InMemorySaver()

        @asynccontextmanager
        async def open_agent(_state):
            yield agent(saver)

        monkeypatch.setattr(bridge, "open_configured_agent", open_agent)
        monkeypatch.setattr(bridge, "notify", lambda *_args: asyncio.sleep(0))
        thread_id, run_id = str(uuid4()), str(uuid4())
        await bridge.loop.dispatch(
            "run.start",
            {
                "thread_id": thread_id,
                "run_id": run_id,
                "text": "pause and stop",
                "selected_plugins": [],
                "selected_skills": [],
            },
        )
        await bridge.loop.active[run_id][1]
        assert bridge.loop.paused_runs[thread_id] == run_id
        assert bridge.loop.turn_id == run_id

        with pytest.raises(ValueError, match="run_id does not match"):
            await bridge.loop.dispatch(
                "run.stop",
                {"run_id": str(uuid4()), "thread_id": thread_id},
            )
        async with open_agent(bridge.loop) as state:
            checkpoint = await state.aget_state(bridge.make_checkpoint_config(thread_id))
            assert checkpoint.tasks[0].interrupts

        await bridge.loop.dispatch(
            "run.stop", {"run_id": run_id, "thread_id": thread_id}
        )
        assert thread_id not in bridge.loop.paused_runs
        assert bridge.loop.turn_id is None
        assert bridge.loop.selected_plugin_ids == frozenset()
        assert bridge.loop.selected_skill_ids == frozenset()

    asyncio.run(scenario())
