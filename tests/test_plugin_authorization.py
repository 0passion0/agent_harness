import pytest

from vertical_harness.engine.policy import PluginAuthorizationGateway
from vertical_plugins.common.contracts import SideEffect


def test_remembered_plugin_grants_preserve_identity_enablement_and_spoof_checks():
    gateway = PluginAuthorizationGateway(enabled_plugins=["demo"], remember_authorizations=True)
    gateway.bind_workflow("workflow", "demo")
    args = {"plugin_id": "demo", "workflow_name": "workflow", "user_id": "user", "tenant_id": "tenant",
            "thread_id": "thread", "run_id": "run", "input_data": {"file_path": "input.csv"}}
    gateway.select_for_run("thread", "run", ["demo"])
    with pytest.raises(PermissionError, match="not authorized"):
        gateway.verify_execution(**args)
    request = gateway.request_execution(
        plugin_id="demo", workflow_name="workflow", purpose="test", input_scope=["input.csv"],
        user_id="user", tenant_id="tenant", thread_id="thread", run_id="run",
    )
    gateway.approve_request(request.request_id, user_id="user", tenant_id="tenant")
    gateway.finish_execution(request.request_id)
    gateway.end_run("thread", "run")
    gateway.verify_execution(**{**args, "thread_id": "new-thread", "run_id": "new-run",
                                "input_data": {"file_path": "next.csv"}, "effect": SideEffect.WRITE})
    for identity in ({"user_id": "other"}, {"tenant_id": "other"}):
        with pytest.raises(PermissionError, match="not authorized"):
            gateway.verify_execution(**{**args, **identity})
    with pytest.raises(PermissionError, match="Spoofed authorization"):
        gateway.verify_execution(**{**args, "input_data": {"approved": True}})
    disabled = PluginAuthorizationGateway(remember_authorizations=True)
    disabled.authorize_plugin("demo", user_id="user", tenant_id="tenant")
    with pytest.raises(PermissionError, match="not enabled"):
        disabled.verify_execution(**args)
