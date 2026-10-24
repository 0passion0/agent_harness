import asyncio
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from test_bridge import configure

from vertical_harness.core import compose
from vertical_harness.core.context import RunContext
from vertical_harness.desktop import AgentLoop
from vertical_harness.models import HarnessSettings
from vertical_harness.runtime.agent_runtime import AgentRuntime
from vertical_harness.runtime.run_manager import RunManager
from vertical_harness.runtime.session_service import SessionService


@pytest.mark.parametrize("tenant,user", [("other", "user"), ("tenant", "other")])
def test_shared_checkpoint_rejects_other_identity_without_changing_data(
    tmp_path, monkeypatch, tenant, user
):
    from test_deepagents_architecture import Model

    model = Model(responses=[AIMessage("private answer")])
    monkeypatch.setattr(compose.ModelRouting, "init_configured_model", lambda _: model)

    async def run():
        configured = HarnessSettings(
            _env_file=None,
            model="test:model",
            workspace_root=tmp_path,
            store_backend="memory",
        )
        owner = await AgentRuntime.create(configured, RunContext("tenant", "user"))
        borrower = AgentRuntime(configured, RunContext(tenant, user))
        borrower.checkpointer, borrower.store = owner.checkpointer, owner.store
        thread_id, run_id = str(uuid4()), str(uuid4())
        config = {
            "configurable": {"thread_id": thread_id},
            "metadata": {
                "desktop_tenant_id": "tenant",
                "desktop_user_id": "user",
                "desktop_run_id": run_id,
            },
        }
        events = []

        async def notify(method, data):
            events.append((method, data))

        sessions = SessionService(borrower)
        manager = RunManager(borrower, sessions, notify)
        try:
            async with owner.open_agent() as graph:
                await graph.ainvoke({"messages": [HumanMessage("private prompt")]}, config)
            for usage_only in [False, True]:
                with pytest.raises(PermissionError, match="identity"):
                    await sessions.snapshot(thread_id, usage_only=usage_only)
            with pytest.raises(PermissionError, match="identity"):
                await sessions.delete(thread_id)
            manager.start(thread_id, str(uuid4()), HumanMessage("overwrite"))
            with pytest.raises(PermissionError, match="identity"):
                await manager.wait()
            assert events[-1][1]["type"] == "run.failed"
            assert "private" not in str(events)
            snapshot = await SessionService(owner).snapshot(thread_id)
            assert [m["data"]["content"] for m in snapshot["messages"]] == [
                "private prompt",
                "private answer",
            ]
            assert len(model.seen) == 1
        finally:
            await manager.cancel()
            await borrower.aclose()
            await owner.aclose()

    asyncio.run(run())


def test_failed_reconfiguration_preserves_working_runtime(tmp_path, monkeypatch):
    loop = AgentLoop()

    async def run():
        await configure(monkeypatch, tmp_path, loop)
        original = loop.runtime
        try:
            await loop.sessions.save_usage("chat", {"sentinel": "keep"})
            with pytest.raises((FileNotFoundError, OSError)):
                await loop.dispatch(
                    "configure",
                    {
                        "tenant_id": "other",
                        "user_id": "other",
                        "workspace_root": str(tmp_path / "other"),
                        "runtime_data_dir": str(tmp_path / "other-runtime"),
                        "options": {
                            "model_code": "test:fake",
                            "permission_mode": "full",
                            "max_context_tokens": 128000,
                            "agent_content": "",
                            "runtimes": {"python": str(tmp_path / "missing.exe"), "node": ""},
                        },
                    },
                )
            assert loop.runtime is original
            item = await original.store.aget(loop.sessions.namespace("chat"), "usage")
            assert item.value == {"sentinel": "keep"}
        finally:
            await loop.aclose()
            await loop.aclose()

    asyncio.run(run())


def test_memory_resources_survive_multiple_operations_and_prestart_cancel(tmp_path):
    async def run():
        runtime = await AgentRuntime.create(
            HarnessSettings(
                _env_file=None, model="test:fake", workspace_root=tmp_path, store_backend="memory"
            ),
            RunContext("tenant", "user"),
        )
        sessions = SessionService(runtime)
        events = []

        async def notify(method, data):
            events.append((method, data))

        manager = RunManager(runtime, sessions, notify)
        try:
            await sessions.save_usage("chat", {"sentinel": True})
            saved = await runtime.store.aget(sessions.namespace("chat"), "usage")
            assert saved.value == {"sentinel": True}
            manager.start(
                str(uuid4()), str(uuid4()), HumanMessage(content="cancel before first scheduling")
            )
            assert await manager.cancel()
            assert not manager.is_running
            assert not await manager.cancel()
            assert events == []
        finally:
            await runtime.aclose()

    asyncio.run(run())
