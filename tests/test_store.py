"""Tests for native LangGraph SQLite Store and Deep Agents CompositeBackend decoupling."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from vertical_harness.contracts import RunContext
from vertical_harness.settings import HarnessSettings
from vertical_harness.store import (
    SqliteStore,
    aopen_store,
    compose_backend,
    open_store,
    tenant_memory_namespace,
)


def test_native_sqlite_store_lifecycle_and_crud(tmp_path: Path) -> None:
    """open_store delegates to official SqliteStore and persists across sessions."""
    settings = HarnessSettings(
        _env_file=None,
        model="deepseek:deepseek-flash",
        workspace_root=tmp_path / "ws",
    )
    ns = ("tenant_alpha", "user_1", "memories")

    with open_store(settings) as store:
        assert isinstance(store, SqliteStore)
        store.put(ns, "pref", {"theme": "dark", "citations": "ieee"})

    with open_store(settings) as store:
        reloaded = store.get(ns, "pref")
        assert reloaded is not None
        assert reloaded.value == {"theme": "dark", "citations": "ieee"}
        store.delete(ns, "pref")
        assert store.get(ns, "pref") is None

    assert (tmp_path / "ws" / "store.sqlite").exists()


def test_native_async_sqlite_store_lifecycle(tmp_path: Path) -> None:
    """aopen_store delegates to official AsyncSqliteStore."""
    settings = HarnessSettings(
        _env_file=None,
        model="deepseek:deepseek-flash",
        store_db_path=tmp_path / "async_store.sqlite",
    )

    async def _run() -> None:
        async with aopen_store(settings) as store:
            ns = ("async_tenant", "user_42", "memories")
            await store.aput(ns, "k1", {"fact": "evidence-first"})
            item = await store.aget(ns, "k1")
            assert item is not None
            assert item.value == {"fact": "evidence-first"}

    asyncio.run(_run())


def test_composite_backend_routes_memories_to_sqlite_store(tmp_path: Path) -> None:
    """CompositeBackend routes /memories/ to native SqliteStore while keeping workspace on disk."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    settings = HarnessSettings(
        _env_file=None,
        model="deepseek:deepseek-flash",
        workspace_root=workspace,
    )
    fake_runtime = SimpleNamespace(
        context=RunContext(tenant_id="lab.a", user_id="researcher.7", task_tier="quality")
    )

    with open_store(settings) as store:
        backend = compose_backend(
            workspace,
            store,
            memory_route="/memories/",
            namespace_factory=lambda _rt: tenant_memory_namespace(fake_runtime),
        )

        ws_write = backend.write("/draft.txt", "Manuscript draft [1]")
        mem_write = backend.write("/memories/AGENTS.md", "# Lab Preferences\nAlways check DOI.")
        assert ws_write.error is None
        assert mem_write.error is None

        # Workspace file exists on disk; memory file lives only in SQLite store
        assert (workspace / "draft.txt").exists()
        assert not (workspace / "memories" / "AGENTS.md").exists()

        # Verify sanitization of '.' in tenant_id/user_id for LangGraph SqliteStore compatibility
        item = store.get(("lab_a", "researcher_7", "memories"), "/AGENTS.md")
        assert item is not None
        assert "Always check DOI." in item.value["content"]

        read_mem = backend.read("/memories/AGENTS.md")
        assert read_mem.error is None
        assert read_mem.file_data is not None
        assert "Always check DOI." in read_mem.file_data["content"]
