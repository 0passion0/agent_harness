from __future__ import annotations

import asyncio
import importlib.util
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph

from vertical_harness import aopen_checkpointer

SPEC = importlib.util.spec_from_file_location("desktop_bridge", Path(__file__).parents[1] / "bridge.py")
sys.path.insert(0, str(Path(__file__).parents[1]))
bridge = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(bridge)

APPROVAL = {"decisions": [{"type": "approve"}]}


def agent(checkpointer=None, body_calls=None):
    def step(_state):
        from langgraph.types import interrupt
        decision = interrupt({"action_requests": [{
            "name": "write_note", "args": {"text": "private note"},
        }]})
        if body_calls is not None:
            body_calls.append(decision)
        return {"messages": [AIMessage(content="approved")]}

    graph = StateGraph(MessagesState)
    graph.add_node("step", step)
    graph.add_edge(START, "step")
    graph.add_edge("step", END)
    return graph.compile(checkpointer=checkpointer or InMemorySaver()).with_config({"recursion_limit": 9999})


async def configure(monkeypatch, tmp_path):
    monkeypatch.setenv("VA_MODEL", "test:fake")
    return await bridge.dispatch("configure", {
        "tenant_id": "tenant-a", "user_id": "user-a",
        "workspace_root": str(tmp_path / "workspace"),
        "runtime_data_dir": str(tmp_path / "runtime"),
        "options": {
            "model_code": "", "exa_api_key": "", "runtimes": {"python": "", "node": ""},
            "permission_mode": "ask", "max_context_tokens": 128000, "agent_content": "",
            "skills": [], "mcp_servers": {}, "analysis_enabled": False,
            "exa_enabled": False, "analysis_plugin_dir": "",
        },
    })


def test_configure_isolates_store_and_checkpoint_paths(monkeypatch, tmp_path):
    result = asyncio.run(configure(monkeypatch, tmp_path))
    assert result["model"] == "test:fake"
    assert "environment" in result
    assert bridge.settings.store_db_path == tmp_path / "runtime" / "store.sqlite"
    assert bridge.settings.checkpoint_db_path == tmp_path / "runtime" / "checkpoints.sqlite"
    assert bridge.settings.resolved_workspace_root == (tmp_path / "workspace").resolve()


def test_snapshot_reopens_sqlite_checkpoint_with_messages_and_interrupt(monkeypatch, tmp_path):
    async def scenario():
        await configure(monkeypatch, tmp_path)
        thread_id = str(uuid4())
        async with aopen_checkpointer(bridge.settings) as saver:
            graph = agent(saver)
            await graph.ainvoke(
                {"messages": [HumanMessage(content="hello")]},
                {"configurable": {"thread_id": thread_id}},
            )

        @asynccontextmanager
        async def open_agent():
            async with aopen_checkpointer(bridge.settings) as saver:
                yield agent(saver)
        monkeypatch.setattr(bridge, "open_configured_agent", open_agent)
        snapshot = await bridge.dispatch("snapshot", {"thread_id": thread_id})
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
        async def open_agent():
            nonlocal context_exited
            try:
                yield graph
            finally:
                context_exited = True
        monkeypatch.setattr(bridge, "open_configured_agent", open_agent)
        events = []
        async def capture(method, params):
            if method == "agent.event" and params["type"] in {"run.completed", "run.interrupted", "run.failed"}:
                assert params["run_id"] not in bridge.active
                assert context_exited
            events.append((method, params))
        monkeypatch.setattr(bridge, "notify", capture)

        thread_id, run_id = str(uuid4()), str(uuid4())
        await bridge.dispatch("run.start", {"thread_id": thread_id, "run_id": run_id, "text": "hello"})
        await bridge.active[run_id][1]
        interrupted = next(p for m, p in events if m == "agent.event" and p["type"] == "run.interrupted")
        interrupt_id = interrupted["data"]["interrupts"][0]["id"]

        resume_id = str(uuid4())
        await bridge.dispatch("run.resume", {
            "thread_id": thread_id, "run_id": resume_id,
            "decisions": {interrupt_id: APPROVAL},
        })
        await bridge.active[resume_id][1]
        assert body_calls == [APPROVAL]
        assert any(m == "agent.event" and p["type"] == "run.completed" for m, p in events)
    asyncio.run(scenario())


def test_stop_cancels_active_run(monkeypatch, tmp_path):
    async def scenario():
        await configure(monkeypatch, tmp_path)
        entered = asyncio.Event()
        @asynccontextmanager
        async def slow_open():
            entered.set()
            await asyncio.Event().wait()
            yield None
        monkeypatch.setattr(bridge, "open_configured_agent", slow_open)
        events = []
        async def capture(method, params):
            events.append((method, params))
        monkeypatch.setattr(bridge, "notify", capture)
        thread_id, run_id = str(uuid4()), str(uuid4())
        await bridge.dispatch("run.start", {"thread_id": thread_id, "run_id": run_id, "text": "cancel"})
        await entered.wait()
        assert await bridge.dispatch("run.stop", {"run_id": run_id, "thread_id": thread_id}) == {}
        assert events == [("run.cancelled", {"run_id": run_id, "thread_id": thread_id})]
    asyncio.run(scenario())
