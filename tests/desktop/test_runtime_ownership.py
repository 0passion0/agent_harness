import asyncio
from uuid import uuid4

import pytest
from langchain_core.messages import HumanMessage
from test_bridge import configure

from vertical_harness.config.settings import HarnessSettings
from vertical_harness.core.context import RunContext
from vertical_harness.desktop import AgentLoop
from vertical_harness.runtime.agent_runtime import AgentRuntime
from vertical_harness.runtime.run_manager import RunManager
from vertical_harness.runtime.session_service import SessionService


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
            assert manager.active is None
            assert not await manager.cancel()
            assert events == []
        finally:
            await runtime.aclose()

    asyncio.run(run())
