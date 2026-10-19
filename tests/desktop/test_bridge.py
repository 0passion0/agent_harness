from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph

from vertical_harness import aopen_checkpointer
from vertical_harness import desktop as bridge

APPROVAL = {"decisions": [{"type": "approve"}]}


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


async def configure(monkeypatch, tmp_path):
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
                "enabled_plugins": [],
            },
        },
    )


def test_configure_isolates_store_and_checkpoint_paths(monkeypatch, tmp_path):
    result = asyncio.run(configure(monkeypatch, tmp_path))
    assert result["model"] == "test:fake"
    assert "environment" in result
    assert bridge.loop.settings.store_db_path == tmp_path / "runtime" / "store.sqlite"
    assert bridge.loop.settings.checkpoint_db_path == tmp_path / "runtime" / "checkpoints.sqlite"
    assert bridge.loop.settings.resolved_workspace_root == (tmp_path / "workspace").resolve()


def test_snapshot_reopens_sqlite_checkpoint_with_messages_and_interrupt(monkeypatch, tmp_path):
    async def scenario():
        await configure(monkeypatch, tmp_path)
        thread_id = str(uuid4())
        async with aopen_checkpointer(bridge.loop.settings) as saver:
            graph = agent(saver)
            await graph.ainvoke(
                {"messages": [HumanMessage(content="hello")]},
                {"configurable": {"thread_id": thread_id}},
            )

        @asynccontextmanager
        async def open_agent(_state):
            async with aopen_checkpointer(bridge.loop.settings) as saver:
                yield agent(saver)

        monkeypatch.setattr(bridge, "open_configured_agent", open_agent)
        snapshot = await bridge.loop.dispatch("snapshot", {"thread_id": thread_id})
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


def test_plugin_scope_approval_survives_checkpoint_reconfigure_for_same_run(
    monkeypatch, tmp_path
):
    async def scenario():
        await configure(monkeypatch, tmp_path)
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

        await configure(monkeypatch, tmp_path)
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
        with pytest.raises(PermissionError, match="expands beyond approved scope"):
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
        assert not gateway.is_authorized(
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
