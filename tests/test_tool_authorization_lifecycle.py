import asyncio
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphInterrupt
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import Command, interrupt

import vertical_harness.engine.policy as policy_module
from vertical_harness.engine.policy import (
    PluginAuthorizationGateway,
    PluginAuthState,
    SupervisorPolicyMiddleware,
)
from vertical_plugins.common.contracts import RunContext, SideEffect, WorkflowContribution


def make_policy(monkeypatch, remember):
    gateway = PluginAuthorizationGateway(enabled_plugins=["demo"], remember_authorizations=remember)
    gateway.bind_workflow("workflow", "demo")
    workflow = WorkflowContribution.from_async_graph(
        name="workflow", description="Run workflow", graph_id="workflow"
    )
    policy = SupervisorPolicyMiddleware([workflow], auth_gateway=gateway)
    approvals = []

    def approve(payload):
        approvals.append(payload["request_id"])
        return {"request_id": payload["request_id"], "approved": True}

    monkeypatch.setattr(policy_module, "interrupt", approve)
    return policy, gateway, approvals


def make_request(call_id):
    return SimpleNamespace(
        tool_call={
            "name": "task",
            "args": {"subagent_type": "workflow", "description": "Run it"},
            "id": call_id,
        },
        state={"messages": [], "thread_id": "thread"},
        runtime=SimpleNamespace(context=SimpleNamespace(user_id="user", tenant_id="tenant")),
        config={"configurable": {"thread_id": "thread"}},
    )


async def assert_new_approval(
    policy, gateway, approvals, call_id, invoke, tool_status, expected_state, remember
):
    request = make_request(call_id)

    def handler(_request):
        assert gateway.get_state("workflow", thread_id="thread") == PluginAuthState.RUNNING
        return ToolMessage(content="done", tool_call_id=call_id, status=tool_status)

    result = await invoke(request, handler)
    assert result.status == tool_status
    assert len(approvals) == 1
    assert gateway.get_state("workflow", thread_id="thread") == expected_state

    next_request = make_request(f"{call_id}-next")

    def next_handler(_request):
        if not remember:
            assert gateway.get_state("workflow", thread_id="thread") == PluginAuthState.RUNNING
        return ToolMessage(content="done", tool_call_id=f"{call_id}-next")

    await invoke(next_request, next_handler)
    assert len(approvals) == (1 if remember else 2)


@pytest.mark.parametrize(
    ("tool_status", "expected_state"),
    [("success", PluginAuthState.COMPLETED), ("error", PluginAuthState.FAILED)],
)
@pytest.mark.parametrize("remember", [False, True])
def test_sync_approved_dispatch_without_selected_run_consumes_approval(
    monkeypatch, tool_status, expected_state, remember
):
    policy, gateway, approvals = make_policy(monkeypatch, remember)

    async def invoke(request, handler):
        return policy.wrap_tool_call(request, handler)

    asyncio.run(
        assert_new_approval(
            policy, gateway, approvals, "sync-call", invoke, tool_status, expected_state, remember
        )
    )


@pytest.mark.parametrize(
    ("tool_status", "expected_state"),
    [("success", PluginAuthState.COMPLETED), ("error", PluginAuthState.FAILED)],
)
@pytest.mark.parametrize("remember", [False, True])
def test_async_approved_dispatch_without_selected_run_consumes_approval(
    monkeypatch, tool_status, expected_state, remember
):
    policy, gateway, approvals = make_policy(monkeypatch, remember)

    async def invoke(request, handler):
        async def async_handler(req):
            return handler(req)

        return await policy.awrap_tool_call(request, async_handler)

    asyncio.run(
        assert_new_approval(
            policy, gateway, approvals, "async-call", invoke, tool_status, expected_state, remember
        )
    )


@pytest.mark.parametrize("use_async", [False, True])
@pytest.mark.parametrize("selected_run", [False, True])
@pytest.mark.parametrize(
    ("error_type", "expected_state"),
    [(RuntimeError, PluginAuthState.FAILED), (asyncio.CancelledError, PluginAuthState.CANCELLED)],
)
def test_escaping_dispatch_preserves_exception_and_finishes_authorization(
    monkeypatch, use_async, selected_run, error_type, expected_state
):
    policy, gateway, approvals = make_policy(monkeypatch, remember=False)
    if selected_run:
        gateway.select_for_run("thread", "run", ["demo"])
    request = make_request("failed-call")
    failure = error_type("workflow stopped")

    def handler(_request):
        assert gateway.get_state("workflow", thread_id="thread") == PluginAuthState.RUNNING
        raise failure

    async def invoke():
        if use_async:
            async def async_handler(req):
                return handler(req)

            return await policy.awrap_tool_call(request, async_handler)
        return policy.wrap_tool_call(request, handler)

    with pytest.raises(error_type) as raised:
        asyncio.run(invoke())
    assert raised.value is failure
    assert gateway.get_state("workflow", thread_id="thread") == expected_state
    assert len(approvals) == 1

    next_request = make_request("next-call")

    async def invoke_next():
        if use_async:
            async def next_handler(_request):
                return ToolMessage(content="done", tool_call_id="next-call")

            return await policy.awrap_tool_call(next_request, next_handler)
        return policy.wrap_tool_call(
            next_request, lambda _request: ToolMessage(content="done", tool_call_id="next-call")
        )

    asyncio.run(invoke_next())
    assert gateway.get_state("workflow", thread_id="thread") == PluginAuthState.COMPLETED
    assert len(approvals) == (1 if selected_run else 2)


@pytest.mark.parametrize("use_async", [False, True])
def test_graph_interrupt_keeps_authorization_resumable(monkeypatch, use_async):
    policy, gateway, approvals = make_policy(monkeypatch, remember=False)
    gateway.select_for_run("thread", "run", ["demo"])
    request = make_request("paused-call")
    interruption = GraphInterrupt()

    def pause(_request):
        raise interruption

    async def invoke(handler):
        if use_async:
            async def async_handler(req):
                return handler(req)

            return await policy.awrap_tool_call(request, async_handler)
        return policy.wrap_tool_call(request, handler)

    with pytest.raises(GraphInterrupt) as raised:
        asyncio.run(invoke(pause))
    assert raised.value is interruption
    assert gateway.get_state("workflow", thread_id="thread") == PluginAuthState.RUNNING
    asyncio.run(invoke(lambda _request: ToolMessage(content="done", tool_call_id="paused-call")))
    assert gateway.get_state("workflow", thread_id="thread") == PluginAuthState.COMPLETED
    assert len(approvals) == 1


@pytest.mark.parametrize("use_async", [False, True])
@pytest.mark.parametrize("handle_tool_errors", [False, True])
def test_native_tool_node_tracks_handled_and_escaping_workflow_errors(
    monkeypatch, use_async, handle_tool_errors
):
    policy, gateway, approvals = make_policy(monkeypatch, remember=False)
    failure = RuntimeError("workflow hook failed")

    @tool("task", description="Run a workflow.")
    def failing_task(subagent_type: str, description: str) -> str:
        raise failure

    builder = StateGraph(MessagesState, context_schema=RunContext)
    builder.add_node("tools", ToolNode(
        [failing_task], handle_tool_errors=handle_tool_errors,
        wrap_tool_call=policy.wrap_tool_call, awrap_tool_call=policy.awrap_tool_call,
    ))
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)
    graph = builder.compile()
    payload = {"messages": [AIMessage(content="", tool_calls=[make_request("native-call").tool_call])]}
    config = {"configurable": {"thread_id": "thread"}}
    context = RunContext(tenant_id="tenant", user_id="user")

    def invoke():
        if use_async:
            return asyncio.run(graph.ainvoke(payload, config=config, context=context))
        return graph.invoke(payload, config=config, context=context)

    if handle_tool_errors:
        result = invoke()
        assert result["messages"][-1].status == "error"
    else:
        with pytest.raises(RuntimeError) as raised:
            invoke()
        assert raised.value is failure
    assert gateway.get_state("workflow", thread_id="thread") == PluginAuthState.FAILED
    assert len(approvals) == 1


@pytest.mark.parametrize("use_async", [False, True])
def test_native_tool_interrupt_resumes_with_the_same_authorization(monkeypatch, use_async):
    policy, gateway, approvals = make_policy(monkeypatch, remember=False)
    gateway.select_for_run("thread", "run", ["demo"])

    @tool("task", description="Run a workflow.")
    def paused_task(subagent_type: str, description: str) -> str:
        return interrupt("Continue workflow?")

    builder = StateGraph(MessagesState, context_schema=RunContext)
    builder.add_node("tools", ToolNode(
        [paused_task], wrap_tool_call=policy.wrap_tool_call, awrap_tool_call=policy.awrap_tool_call,
    ))
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)
    graph = builder.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "thread"}}
    context = RunContext(tenant_id="tenant", user_id="user")

    def invoke(payload):
        if use_async:
            return asyncio.run(graph.ainvoke(payload, config=config, context=context))
        return graph.invoke(payload, config=config, context=context)

    initial = invoke({"messages": [AIMessage(content="", tool_calls=[make_request("native-pause").tool_call])]})
    assert initial["__interrupt__"]
    assert gateway.get_state("workflow", thread_id="thread") == PluginAuthState.RUNNING
    resumed = invoke(Command(resume="Continue"))
    assert resumed["messages"][-1].content == "Continue"
    assert gateway.get_state("workflow", thread_id="thread") == PluginAuthState.COMPLETED
    assert len(approvals) == 1


@pytest.mark.parametrize("selected_run", [False, True])
def test_cancelling_active_dispatch_finishes_authorization(monkeypatch, selected_run):
    policy, gateway, approvals = make_policy(monkeypatch, remember=False)
    if selected_run:
        gateway.select_for_run("thread", "run", ["demo"])

    async def run():
        started = asyncio.Event()
        waiting = asyncio.Event()

        async def handler(_request):
            started.set()
            await waiting.wait()

        task = asyncio.create_task(policy.awrap_tool_call(make_request("cancel-call"), handler))
        await started.wait()
        assert gateway.get_state("workflow", thread_id="thread") == PluginAuthState.RUNNING
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert gateway.get_state("workflow", thread_id="thread") == PluginAuthState.CANCELLED
    assert len(approvals) == 1


@pytest.mark.parametrize("use_async", [False, True])
@pytest.mark.parametrize("selected_run", [False, True])
@pytest.mark.parametrize("preapproved", [False, True])
@pytest.mark.parametrize("gate", ["dependency", "cache", "duplicate", "limit"])
def test_short_circuit_does_not_start_or_consume_authorization(
    monkeypatch, use_async, selected_run, preapproved, gate
):
    _, gateway, approvals = make_policy(monkeypatch, remember=False)
    workflow = WorkflowContribution.from_async_graph(
        name="workflow", description="Run workflow", graph_id="workflow",
        depends_on=("prerequisite",) if gate == "dependency" else (),
        idempotent=gate in {"cache", "duplicate"},
        max_dispatches=1 if gate == "limit" else 5,
    )
    prerequisite = WorkflowContribution.from_async_graph(
        name="prerequisite", description="Prepare workflow", graph_id="prerequisite"
    )
    policy = SupervisorPolicyMiddleware([workflow, prerequisite], auth_gateway=gateway)
    if selected_run:
        gateway.select_for_run("thread", "run", ["demo"])
    if preapproved:
        pending = gateway.request_execution(
            plugin_id="demo", workflow_name="workflow", purpose="Run it", input_scope=(),
            allowed_effects=(SideEffect.READ,), user_id="user", tenant_id="tenant",
            thread_id="thread", run_id="run" if selected_run else None,
        )
        gateway.approve_request(pending.request_id, user_id="user", tenant_id="tenant")

    request = make_request("blocked-call")
    previous = make_request("previous-call").tool_call
    if gate in {"cache", "limit"}:
        request.state["messages"] = [
            AIMessage(content="", tool_calls=[previous]),
            ToolMessage(content="cached output", tool_call_id="previous-call"),
        ]
    elif gate == "duplicate":
        request.state["messages"] = [
            AIMessage(content="", tool_calls=[previous, request.tool_call]),
        ]

    async def invoke(req, handler):
        if use_async:
            async def async_handler(item):
                return handler(item)

            return await policy.awrap_tool_call(req, async_handler)
        return policy.wrap_tool_call(req, handler)

    def unexpected_handler(_request):
        pytest.fail("A short-circuited workflow must not execute")

    result = asyncio.run(invoke(request, unexpected_handler))
    assert result.status == ("success" if gate == "cache" else "error")
    if gate == "cache":
        assert result.content == "cached output"
    assert result.artifact["supervisor_policy"]["dispatched"] is False
    assert gateway.get_state("workflow", thread_id="thread") == PluginAuthState.AUTHORIZED
    assert "blocked-call" not in gateway._execution_requests
    assert len(approvals) == (0 if preapproved else 1)

    next_request = make_request("next-call")
    next_request.tool_call["args"]["description"] = "Run next"
    preparation = make_request("prepared-call").tool_call
    preparation["args"]["subagent_type"] = "prerequisite"
    next_request.state["messages"] = [
        AIMessage(content="", tool_calls=[preparation]),
        ToolMessage(content="prepared", tool_call_id="prepared-call"),
        HumanMessage(content="Run next"),
    ]

    def execute(_request):
        assert gateway.get_state("workflow", thread_id="thread") == PluginAuthState.RUNNING
        return ToolMessage(content="done", tool_call_id="next-call")

    asyncio.run(invoke(next_request, execute))
    assert gateway.get_state("workflow", thread_id="thread") == PluginAuthState.COMPLETED
    assert "next-call" not in gateway._execution_requests
    assert len(approvals) == (0 if preapproved else 1)


@pytest.mark.parametrize("use_async", [False, True])
@pytest.mark.parametrize("expanded_scope", [False, True])
def test_cached_output_still_requires_matching_approval(monkeypatch, use_async, expanded_scope):
    _, gateway, _ = make_policy(monkeypatch, remember=False)
    workflow = WorkflowContribution.from_async_graph(
        name="workflow", description="Run workflow", graph_id="workflow", idempotent=True,
    )
    policy = SupervisorPolicyMiddleware([workflow], auth_gateway=gateway)
    if expanded_scope:
        pending = gateway.request_execution(
            plugin_id="demo", workflow_name="workflow", purpose="Read approved data",
            input_scope=("approved.csv",), allowed_effects=(SideEffect.READ,),
            user_id="user", tenant_id="tenant", thread_id="thread",
        )
        gateway.approve_request(pending.request_id, user_id="user", tenant_id="tenant")
    requests = []

    def reject(payload):
        requests.append(payload)
        return {"request_id": payload["request_id"], "approved": False}

    monkeypatch.setattr(policy_module, "interrupt", reject)
    request = make_request("cached-call")
    request.tool_call["args"]["description"] = '{"file_path": "other.csv"}'
    previous = {**request.tool_call, "id": "previous-call"}
    request.state["messages"] = [
        AIMessage(content="", tool_calls=[previous]),
        ToolMessage(content="cached secret", tool_call_id="previous-call"),
    ]

    def unexpected_handler(_request):
        pytest.fail("A rejected cached result must not execute")

    async def invoke():
        if use_async:
            async def handler(req):
                return unexpected_handler(req)

            return await policy.awrap_tool_call(request, handler)
        return policy.wrap_tool_call(request, unexpected_handler)

    result = asyncio.run(invoke())
    assert result.status == "error"
    assert "cached secret" not in result.content
    assert len(requests) == 1
    assert requests[0]["input_scope"] == ["other.csv"]


@pytest.mark.parametrize("use_async", [False, True])
def test_cached_output_preserves_completed_selected_run_authorization(monkeypatch, use_async):
    _, gateway, approvals = make_policy(monkeypatch, remember=False)
    gateway.select_for_run("thread", "run", ["demo"])
    workflow = WorkflowContribution.from_async_graph(
        name="workflow", description="Run workflow", graph_id="workflow", idempotent=True,
    )
    policy = SupervisorPolicyMiddleware([workflow], auth_gateway=gateway)

    async def invoke(request, handler):
        if use_async:
            async def async_handler(req):
                return handler(req)

            return await policy.awrap_tool_call(request, async_handler)
        return policy.wrap_tool_call(request, handler)

    first = make_request("first-call")
    result = asyncio.run(invoke(
        first, lambda _request: ToolMessage(content="done", tool_call_id="first-call")
    ))
    assert gateway.get_state("workflow", thread_id="thread") == PluginAuthState.COMPLETED
    cached = make_request("cached-call")
    cached.state["messages"] = [AIMessage(content="", tool_calls=[first.tool_call]), result]

    def unexpected_handler(_request):
        pytest.fail("A cached result must not execute")

    reused = asyncio.run(invoke(cached, unexpected_handler))
    assert reused.content == "done"
    assert reused.artifact["supervisor_policy"]["dispatched"] is False
    assert gateway.get_state("workflow", thread_id="thread") == PluginAuthState.COMPLETED
    assert "cached-call" not in gateway._execution_requests
    assert len(approvals) == 1


def test_duplicate_dispatch_preserves_an_already_running_sibling(monkeypatch):
    _, gateway, approvals = make_policy(monkeypatch, remember=False)
    gateway.select_for_run("thread", "run", ["demo"])
    workflow = WorkflowContribution.from_async_graph(
        name="workflow", description="Run workflow", graph_id="workflow", idempotent=True,
    )
    policy = SupervisorPolicyMiddleware([workflow], auth_gateway=gateway)
    first, duplicate = make_request("first-call"), make_request("duplicate-call")
    messages = [AIMessage(content="", tool_calls=[first.tool_call, duplicate.tool_call])]
    first.state["messages"] = duplicate.state["messages"] = messages

    async def run():
        started, release = asyncio.Event(), asyncio.Event()

        async def handler(_request):
            started.set()
            await release.wait()
            return ToolMessage(content="done", tool_call_id="first-call")

        async def unexpected_handler(_request):
            pytest.fail("A duplicate task must not execute")

        active = asyncio.create_task(policy.awrap_tool_call(first, handler))
        await asyncio.wait_for(started.wait(), timeout=5)
        rejected = await policy.awrap_tool_call(duplicate, unexpected_handler)
        assert rejected.status == "error"
        assert gateway.get_state("workflow", thread_id="thread") == PluginAuthState.RUNNING
        assert "first-call" in gateway._execution_requests
        assert "duplicate-call" not in gateway._execution_requests
        release.set()
        await active

    asyncio.run(run())
    assert gateway.get_state("workflow", thread_id="thread") == PluginAuthState.COMPLETED
    assert len(approvals) == 1
