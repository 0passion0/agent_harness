"""Decoupled Store & Backend composition using native LangGraph SQLite persistence."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from deepagents.backends import CompositeBackend, FilesystemBackend, StoreBackend
from deepagents.backends.protocol import BackendProtocol
from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore
from langgraph.store.sqlite import AsyncSqliteStore, SqliteStore

if TYPE_CHECKING:
    from langgraph.runtime import Runtime

    from vertical_harness.contracts import RunContext
    from vertical_harness.settings import HarnessSettings

# LangGraph's native SqliteStore joins namespace segments with '.', so '.' is forbidden inside a segment.
_UNSAFE_NS_CHARS = re.compile(r"[^A-Za-z0-9\-_@+:~]")


def _sanitize_segment(value: str | None, fallback: str = "default") -> str:
    """Normalize runtime identifiers into valid LangGraph & StoreBackend namespace labels."""
    if not value:
        return fallback
    cleaned = _UNSAFE_NS_CHARS.sub("_", value.strip())
    return cleaned or fallback


def tenant_memory_namespace(
    runtime: Runtime[RunContext] | None = None,
    *,
    bucket: str = "memories",
) -> tuple[str, ...]:
    """Derive a tenant- and user-isolated namespace tuple from LangGraph Runtime."""
    context = getattr(runtime, "context", None) if runtime is not None else None
    return (
        _sanitize_segment(getattr(context, "tenant_id", None), "default"),
        _sanitize_segment(getattr(context, "user_id", None), "default"),
        _sanitize_segment(bucket, "memories"),
    )


def _prepare_conn_string(settings: HarnessSettings) -> str:
    """Ensure parent directory exists for file-backed SQLite databases and return conn string."""
    target = settings.resolved_store_target
    if isinstance(target, Path):
        target.parent.mkdir(parents=True, exist_ok=True)
        return str(target)
    return target


def create_store(settings: HarnessSettings) -> BaseStore:
    """Create a ready-to-use native LangGraph ``BaseStore`` from validated settings."""
    if settings.store_backend == "memory":
        return InMemoryStore()
    conn = sqlite3.connect(
        _prepare_conn_string(settings),
        check_same_thread=False,
        isolation_level=None,
    )
    store = SqliteStore(conn)
    store.setup()
    return store


@contextmanager
def open_store(settings: HarnessSettings) -> Iterator[BaseStore]:
    """Context-managed synchronous store lifecycle using native ``SqliteStore``."""
    if settings.store_backend == "memory":
        yield InMemoryStore()
        return
    with SqliteStore.from_conn_string(_prepare_conn_string(settings)) as store:
        store.setup()
        yield store


@asynccontextmanager
async def aopen_store(settings: HarnessSettings) -> AsyncIterator[BaseStore]:
    """Context-managed asynchronous store lifecycle using native ``AsyncSqliteStore``."""
    if settings.store_backend == "memory":
        yield InMemoryStore()
        return
    async with AsyncSqliteStore.from_conn_string(_prepare_conn_string(settings)) as store:
        await store.setup()
        yield store


def compose_backend(
    workspace_root: Path,
    store: BaseStore | None = None,
    *,
    memory_route: str = "/memories/",
    namespace_factory: Callable[[Runtime[Any]], tuple[str, ...]] = tenant_memory_namespace,
) -> BackendProtocol:
    """Mount native ``FilesystemBackend`` with ``StoreBackend`` via ``CompositeBackend``."""
    fs_backend = FilesystemBackend(root_dir=str(workspace_root), virtual_mode=True)
    if store is None:
        return fs_backend

    route = "/" + memory_route.strip("/") + "/"
    return CompositeBackend(
        default=fs_backend,
        routes={route: StoreBackend(store=store, namespace=namespace_factory)},
    )


__all__ = [
    "AsyncSqliteStore",
    "SqliteStore",
    "aopen_store",
    "compose_backend",
    "create_store",
    "open_store",
    "tenant_memory_namespace",
]
