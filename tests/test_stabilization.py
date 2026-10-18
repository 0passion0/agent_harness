"""Regression tests for architecture-review correctness boundaries."""

import asyncio
import threading
from contextlib import asynccontextmanager, contextmanager
from types import SimpleNamespace
from typing import TypedDict

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.store.memory import InMemoryStore
from pydantic import Field

from vertical_harness import HarnessSettings, RunContext, WorkflowContribution
from vertical_harness.engine import compose, routing
from vertical_harness.engine.policy import SupervisorPolicyMiddleware, _inspect_history
from vertical_harness.engine.time_travel import afork_checkpoint, fork_checkpoint
from vertical_harness.plugins.contracts import (
    PluginContribution,
    PluginManifest,
    WorkflowInputState,
    WorkflowInternalState,
    WorkflowOutputState,
)
from vertical_harness.storage.namespace import copy_memory_namespace, tenant_memory_namespace


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


def test_identity_namespaces_and_explicit_copy():
    store = InMemoryStore()
    identities = ["lab.a", "lab_a", "实验室", " lab.a "]
    namespaces = [
        tenant_memory_namespace(SimpleNamespace(context=RunContext(t, "user"))) for t in identities
    ]
    assert len(set(namespaces)) == len(identities)
    store.put(namespaces[0], "private", {"value": 1})
    assert store.get(namespaces[1], "private") is None
    with pytest.raises(ValueError, match="nonempty"):
        tenant_memory_namespace()
    with pytest.raises(ValueError, match="nonempty"):
        RunContext(" ", "user")
    legacy = ("lab_a", "user", "memories")
    store.put(legacy, "old", {"value": 2})
    assert copy_memory_namespace(store, legacy, namespaces[0]) == 1
    assert copy_memory_namespace(store, legacy, namespaces[0]) == 1
    assert store.get(legacy, "old") is not None
    store.put(namespaces[0], "old", {"value": 3})
    with pytest.raises(ValueError, match="conflicting"):
        copy_memory_namespace(store, legacy, namespaces[0])


@pytest.mark.parametrize("asynchronous", [False, True])
def test_supervisor_cannot_read_or_delete_and_does_not_affect_normal_agent(
    tmp_path, monkeypatch, asynchronous
):
    target = tmp_path / "private.txt"
    target.write_text("private-sentinel", encoding="utf-8")
    model = RecordingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "read_file", "args": {"file_path": "/private.txt"}, "id": "read"},
                    {"name": "delete", "args": {"file_path": "/private.txt"}, "id": "delete"},
                ],
            ),
            AIMessage(content="done"),
        ]
    )
    monkeypatch.setattr(compose, "init_configured_model", lambda *_: model)
    agent = compose.build_agent(settings(tmp_path, supervisor_only=True))
    args = ({"messages": [HumanMessage(content="inspect")]},)
    kwargs = {"config": {"configurable": {"thread_id": "s"}}, "context": RunContext("t", "u")}
    result = (
        asyncio.run(agent.ainvoke(*args, **kwargs))
        if asynchronous
        else agent.invoke(*args, **kwargs)
    )
    assert target.exists()
    assert all(
        "private-sentinel" not in str(m.content)
        for m in result["messages"]
        if isinstance(m, ToolMessage)
    )
    assert (
        not set(model.seen_tools[-1] if model.seen_tools else [])
        & compose.SUPERVISOR_EXCLUDED_FS_TOOLS
    )
    normal = RecordingModel(responses=[AIMessage(content="ok")])
    monkeypatch.setattr(compose, "init_configured_model", lambda *_: normal)
    compose.build_agent(settings(tmp_path)).invoke(*args, **kwargs)
    assert "read_file" in normal.seen_tools[-1]


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
@pytest.mark.parametrize("idempotent", [False, True])
def test_parallel_dispatch_has_one_execution(tmp_path, monkeypatch, asynchronous, idempotent):
    calls = []
    builder = StateGraph(
        WorkflowInternalState, input_schema=WorkflowInputState, output_schema=WorkflowOutputState
    )

    def work(state):
        calls.append(1)
        return {"messages": [AIMessage(content='{"ok":true}')]}

    builder.add_node("work", work)
    builder.add_edge(START, "work")
    builder.add_edge("work", END)
    wf = WorkflowContribution.from_compiled_graph(
        name="counted",
        description="Count work",
        runnable=builder.compile(),
        max_dispatches=5 if idempotent else 1,
        idempotent=idempotent,
    )
    plugin = SimpleNamespace(
        manifest=PluginManifest(plugin_id="test.count", version="0.1.0", description="Count work"),
        build=lambda _: PluginContribution(workflows=[wf]),
    )
    monkeypatch.setattr(compose, "discover_plugins", lambda _: [plugin])
    model = RecordingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {
                            "subagent_type": "counted",
                            "description": "same work" if idempotent else f"work {i}",
                        },
                        "id": f"call-{i}",
                    }
                    for i in range(2)
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {
                            "subagent_type": "counted",
                            "description": "same work" if idempotent else "work 0",
                        },
                        "id": "retry",
                    }
                ],
            ),
            AIMessage(content="done"),
        ]
    )
    monkeypatch.setattr(compose, "init_configured_model", lambda *_: model)
    agent = compose.build_agent(settings(tmp_path))
    inputs = {"messages": [HumanMessage(content="run")]}
    kwargs = {
        "config": {"configurable": {"thread_id": "parallel"}},
        "context": RunContext("t", "u"),
    }
    result = (
        asyncio.run(agent.ainvoke(inputs, **kwargs))
        if asynchronous
        else agent.invoke(inputs, **kwargs)
    )
    assert len(calls) == 1
    assert _inspect_history(result["messages"])[0]["counted"] == 1


def test_async_launch_is_not_completion_and_unsupported_contracts_fail_early(tmp_path):
    wf = WorkflowContribution.from_async_graph(
        name="prepare", description="Prepare data", graph_id="prepare", idempotent=True
    )
    history = [
        AIMessage(
            content="",
            tool_calls=[
                {"name": "start_async_task", "args": {"subagent_type": "prepare"}, "id": "start"}
            ],
        ),
        ToolMessage(content="Launched async subagent. task_id: id", tool_call_id="start"),
    ]
    assert "prepare" not in _inspect_history(history)[1]
    composer = compose.AgentComposer(settings(tmp_path))
    composer.workflows[wf.name] = wf
    with pytest.raises(ValueError, match="Async workflow"):
        composer.validate_workflows()
    request = SimpleNamespace(
        tool_call={"name": "start_async_task", "args": {"subagent_type": "prepare"}, "id": "start"},
        state={"messages": []},
    )
    assert SupervisorPolicyMiddleware([wf])._pre_dispatch_check(request).status == "error"


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("persistent", [False, True])
def test_cross_thread_fork_preserves_completed_pending_node(tmp_path, asynchronous, persistent):
    class State(TypedDict):
        a: int
        b: int

    executions = []
    fail = True

    def good(state):
        executions.append(1)
        return {"a": 1}

    def bad(state):
        if fail:
            raise RuntimeError("expected node failure")
        return {"b": 1}

    builder = StateGraph(State)
    builder.add_node("good", good)
    builder.add_node("bad", bad)
    for name in ("good", "bad"):
        builder.add_edge(START, name)
        builder.add_edge(name, END)
    source = {"configurable": {"thread_id": "source"}}

    async def exercise(saver):
        nonlocal fail
        graph = builder.compile(checkpointer=saver)
        with pytest.raises(RuntimeError, match="expected node failure"):
            if asynchronous:
                await graph.ainvoke({"a": 0, "b": 0}, source, durability="sync")
            else:
                graph.invoke({"a": 0, "b": 0}, source, durability="sync")
        fork = (
            await afork_checkpoint(graph, "source", fork_thread_id="fork")
            if asynchronous
            else fork_checkpoint(graph, "source", fork_thread_id="fork")
        )
        saved = await saver.aget_tuple(fork) if asynchronous else saver.get_tuple(fork)
        assert saved.pending_writes
        fail = False
        # Addressing a checkpoint ID explicitly requests replay in LangGraph.
        resume = {"configurable": {"thread_id": "fork"}}
        if asynchronous:
            await graph.ainvoke(None, resume, durability="sync")
        else:
            graph.invoke(None, resume, durability="sync")
        assert len(executions) == 1
        with pytest.raises(ValueError, match="already exists"):
            if asynchronous:
                await afork_checkpoint(graph, "source", fork_thread_id="fork")
            else:
                fork_checkpoint(graph, "source", fork_thread_id="fork")

    async def run():
        if not persistent:
            await exercise(InMemorySaver())
        elif asynchronous:
            async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "fork.sqlite")) as saver:
                await exercise(saver)
        else:
            with SqliteSaver.from_conn_string(str(tmp_path / "fork.sqlite")) as saver:
                await exercise(saver)

    asyncio.run(run())


def test_private_data_paths_and_legacy_migration_guard(tmp_path):
    config = settings(tmp_path / "workspace")
    assert config.enabled_plugins == []
    assert not config.resolved_store_target.is_relative_to(config.resolved_workspace_root)
    config.resolved_workspace_root.mkdir()
    legacy = config.resolved_workspace_root / "store.sqlite"
    legacy.write_bytes(b"old-data")
    with pytest.raises(ValueError, match=r"Legacy store\.sqlite"):
        _ = config.resolved_store_target
    explicit = config.model_copy(update={"store_db_path": legacy})
    assert explicit.resolved_store_target == legacy
    assert legacy.read_bytes() == b"old-data"


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


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("build_fails", [False, True])
def test_resource_plugins_close_once_and_receive_isolated_config(
    tmp_path, monkeypatch, asynchronous, build_fails
):
    from vertical_harness.plugins.contracts import BuildServices

    events = []
    config = settings(tmp_path).model_copy(
        update={
            "enabled_plugins": ["test.resource"],
            "plugin_config": {"test.resource": {"endpoint": "local"}},
        }
    )

    def contribution(services):
        received = services.config_for("test.resource")
        assert received == {"endpoint": "local"}
        received["endpoint"] = "changed"
        assert services.config_for("test.resource")["endpoint"] == "local"
        return PluginContribution()

    @contextmanager
    def resource(services):
        events.append("open")
        try:
            yield contribution(services)
        finally:
            events.append("close")

    @asynccontextmanager
    async def aresource(services):
        with resource(services) as value:
            yield value

    plugin = SimpleNamespace(
        manifest=PluginManifest(
            plugin_id="test.resource", version="0.1.0", description="Resource plugin"
        ),
        build=lambda _: PluginContribution(),
        **({"aopen": aresource} if asynchronous else {"open": resource}),
    )
    monkeypatch.setattr(compose, "discover_plugins", lambda _: [plugin])

    def model(*_):
        if build_fails:
            raise RuntimeError("expected build failure")
        return RecordingModel(responses=[AIMessage(content="ok")])

    monkeypatch.setattr(compose, "init_configured_model", model)

    async def run():
        async with compose.aopen_agent(config):
            assert events == ["open"]

    def invoke():
        if asynchronous:
            asyncio.run(run())
        else:
            with compose.open_agent(config):
                assert events == ["open"]

    if build_fails:
        with pytest.raises(RuntimeError, match="expected build failure"):
            invoke()
    else:
        invoke()
    assert events == ["open", "close"]
    assert BuildServices(tmp_path).config_for("missing") == {}
    if asynchronous and not build_fails:
        from vertical_harness.cli import _print_history

        _print_history(SimpleNamespace(thread_id="history"), config)
        assert events == ["open", "close", "open", "close"]


def test_graph_dependencies_and_reserved_middleware_fail_early(tmp_path):
    from langchain.agents.middleware import AgentMiddleware

    from vertical_harness.plugins.contracts import BuildServices, WorkflowMode

    a = WorkflowContribution.from_async_graph(
        name="first", description="First graph", graph_id="first"
    )
    b = a.model_copy(update={"name": "second", "depends_on": ("first",)})
    composer = compose.AgentComposer(settings(tmp_path))
    composer.workflows = {b.name: b.model_copy(update={"depends_on": ("missing",)})}
    with pytest.raises(ValueError, match="Missing workflow"):
        composer.validate_workflows()
    # Compiled mode keeps this test independent of async restrictions.
    a = a.model_copy(update={"mode": WorkflowMode.COMPILED, "depends_on": ("second",)})
    b = b.model_copy(update={"mode": WorkflowMode.COMPILED})
    composer.workflows = {a.name: a, b.name: b}
    with pytest.raises(ValueError, match="cycle"):
        composer.validate_workflows()

    class FilesystemMiddleware(AgentMiddleware):
        pass

    plugin = SimpleNamespace(
        manifest=PluginManifest(plugin_id="test.bad", version="0.1.0", description="Bad plugin"),
        build=lambda _: PluginContribution(middleware=[FilesystemMiddleware()]),
    )
    with pytest.raises(ValueError, match="Reserved/duplicate"):
        compose.AgentComposer(settings(tmp_path)).register_plugin(plugin, BuildServices(tmp_path))


def test_citation_huge_numbers_are_explicit_invalid_input():
    from research_citation import build_citation_workflow, scan_numeric

    payload = {"manuscript_text": "[" + "9" * 5000 + "]", "bibliography": ["ref"]}
    assert "at most 9 digits" in scan_numeric.invoke(payload)
    result = build_citation_workflow().invoke(payload)
    assert result["structured_response"]["status"] == "invalid_input"
    assert "at most 9 digits" in result["structured_response"]["error"]


def test_successful_tool_scores_do_not_count_denied_attempts():
    from vertical_harness.evaluation.scoring import deterministic_scores, normalize_outputs

    calls = AIMessage(content="", tool_calls=[{"name": "write", "args": {}, "id": "denied"}])
    messages = [
        calls,
        ToolMessage(content="denied", tool_call_id="denied", status="error"),
        AIMessage(content="done"),
    ]
    reference = {"required_tools": ["write"], "required_successful_tools": ["write"]}
    output = normalize_outputs({"messages": messages})
    scores = {s["key"]: s["score"] for s in deterministic_scores(output, reference)}
    assert scores["required_tools"] == 1 and scores["required_successful_tools"] == 0
    messages[1] = ToolMessage(content="written", tool_call_id="denied", status="success")
    assert deterministic_scores({"messages": messages}, reference)[-1]["score"] == 1


def test_cancelled_memory_worker_drains_before_owner_can_close():
    from vertical_harness.engine.memory import _run_sync_owned

    started = threading.Event()
    release = threading.Event()
    completed = threading.Event()

    def work():
        started.set()
        release.wait(timeout=5)
        completed.set()

    async def run():
        task = asyncio.create_task(_run_sync_owned(work))
        await asyncio.to_thread(started.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert completed.is_set()

    asyncio.run(run())


def test_deadline_cancels_async_tool_and_resource_context(tmp_path, monkeypatch):
    from langchain_core.tools import tool

    from vertical_harness.plugins.contracts import ToolContribution

    events = []

    @tool
    async def slow_work() -> str:
        """Wait for an external service."""
        try:
            await asyncio.sleep(60)
        finally:
            events.append("cancelled")
        return "never"

    @asynccontextmanager
    async def resource(_):
        events.append("open")
        try:
            yield PluginContribution(tools=[ToolContribution(tool=slow_work)])
        finally:
            events.append("close")

    plugin = SimpleNamespace(
        manifest=PluginManifest(plugin_id="test.slow", version="0.1.0", description="Slow plugin"),
        build=lambda _: PluginContribution(),
        aopen=resource,
    )
    monkeypatch.setattr(compose, "discover_plugins", lambda _: [plugin])
    model = RecordingModel(
        responses=[
            AIMessage(content="", tool_calls=[{"name": "slow_work", "args": {}, "id": "slow"}]),
            AIMessage(content="done"),
        ]
    )
    monkeypatch.setattr(compose, "init_configured_model", lambda *_: model)

    async def run():
        async with compose.aopen_agent(settings(tmp_path, run_timeout_seconds=0.5)) as agent:
            with pytest.raises(TimeoutError):
                from vertical_harness import ainvoke_agent

                await ainvoke_agent(
                    agent,
                    {"messages": [HumanMessage(content="run")]},
                    config={"configurable": {"thread_id": "timeout"}},
                    context=RunContext("t", "u"),
                )

    asyncio.run(run())
    assert events == ["open", "cancelled", "close"]


def test_memory_backlog_and_receipt_retention_are_bounded(tmp_path):
    import time

    from vertical_harness.engine.memory import _pending_events, _prune_receipts

    store = InMemoryStore()
    ns = ("harness_memory", "tenant", "user")
    for number in reversed(range(205)):
        value = {"text": str(number), "recorded_at": number}
        store.put((*ns, "inbox"), str(number), value)
        store.put((*ns, "events"), str(number), {"recorded_at": number})
    events = _pending_events(store, (*ns, "inbox"))
    assert len(events) == 50
    assert [item.value["recorded_at"] for item in events] == list(range(50))
    for number in range(204):
        store.delete((*ns, "inbox"), str(number))
    store.put((*ns, "events"), "recent", {"recorded_at": time.time_ns()})
    _prune_receipts(settings(tmp_path), store, ns)
    assert {item.key for item in store.search((*ns, "events"))} == {"204", "recent"}
