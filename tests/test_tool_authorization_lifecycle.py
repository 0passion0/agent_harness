import asyncio
from types import SimpleNamespace

import pytest
from langchain_core.messages import ToolMessage

import vertical_harness.engine.policy as policy_module
from vertical_harness.engine.policy import (
    PluginAuthorizationGateway,
    PluginAuthState,
    SupervisorPolicyMiddleware,
)
from vertical_plugins.common.contracts import WorkflowContribution


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
