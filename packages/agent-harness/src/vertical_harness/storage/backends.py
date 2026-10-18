"""解耦的 Store 与 Backend 组合模块：基于原生 LangGraph SQLite 实现数据持久化与虚拟文件系统挂载。"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from deepagents.backends import CompositeBackend, FilesystemBackend, StoreBackend
from deepagents.backends.protocol import BackendProtocol
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore
from langgraph.store.sqlite import AsyncSqliteStore, SqliteStore

from vertical_harness.config.settings import StoreBackendType
from vertical_harness.storage.namespace import tenant_memory_namespace

if TYPE_CHECKING:
    from langgraph.runtime import Runtime

    from vertical_harness.config.settings import HarnessSettings

logger = logging.getLogger(__name__)


def _prepare_conn_string(settings: HarnessSettings) -> str:
    """确保基于文件的 SQLite 数据库父目录存在，并返回连接字符串。

    :param settings: 经校验的宿主配置对象。
    :return: SQLite 连接字符串或数据库物理文件路径字符串。
    """
    logger.debug("preparing_store_connection_string")
    target = settings.resolved_store_target
    if isinstance(target, Path):
        target.parent.mkdir(parents=True, exist_ok=True)
        return str(target)
    return target


def _prepare_checkpoint_conn_string(settings: HarnessSettings) -> str:
    """确保基于文件的 Checkpointer SQLite 数据库父目录存在，并返回连接字符串。

    :param settings: 经校验的宿主配置对象。
    :return: Checkpointer SQLite 连接字符串或数据库物理文件路径字符串。
    """
    logger.debug("preparing_checkpoint_connection_string")
    target = settings.resolved_checkpoint_target
    if isinstance(target, Path):
        target.parent.mkdir(parents=True, exist_ok=True)
        return str(target)
    return target


def create_store(settings: HarnessSettings) -> BaseStore:
    """根据已验证的配置创建立即可用的原生 LangGraph BaseStore 存储实例。

    :param settings: 经校验的宿主配置对象。
    :return: 已执行初始化 setup() 的 BaseStore 存储实例（InMemoryStore 或 SqliteStore）。
    """
    if settings.store_backend == StoreBackendType.MEMORY:
        logger.debug("store_created", extra={"event_fields": {"backend": "memory"}})
        return InMemoryStore()
    logger.debug("store_opening", extra={"event_fields": {"backend": "sqlite"}})
    conn = sqlite3.connect(
        _prepare_conn_string(settings),
        check_same_thread=False,
        isolation_level=None,
    )
    try:
        store = SqliteStore(conn)
        store.setup()
    except Exception:
        conn.close()
        raise
    logger.info("store_ready", extra={"event_fields": {"backend": "sqlite"}})
    return store


@contextmanager
def open_store(settings: HarnessSettings) -> Iterator[BaseStore]:
    """基于上下文管理器管理原生 SqliteStore 的同步生命周期。

    :param settings: 经校验的宿主配置对象。
    :return: 产生已完成 setup() 的 BaseStore 存储实例迭代器。
    """
    backend = settings.store_backend
    logger.debug("store_opening", extra={"event_fields": {"backend": backend}})
    try:
        if backend == StoreBackendType.MEMORY:
            logger.info("store_ready", extra={"event_fields": {"backend": backend}})
            yield InMemoryStore()
        else:
            with SqliteStore.from_conn_string(_prepare_conn_string(settings)) as store:
                store.setup()
                logger.info("store_ready", extra={"event_fields": {"backend": backend}})
                yield store
    finally:
        # 调用方异常也会经过这里；不将其误报为数据库故障。
        logger.debug("store_context_exited", extra={"event_fields": {"backend": backend}})


@asynccontextmanager
async def aopen_store(settings: HarnessSettings) -> AsyncIterator[BaseStore]:
    """基于异步上下文管理器管理原生 AsyncSqliteStore 的异步生命周期。

    :param settings: 经校验的宿主配置对象。
    :return: 产生已完成异步 setup() 的 BaseStore 存储实例异步迭代器。
    """
    backend = settings.store_backend
    logger.debug("async_store_opening", extra={"event_fields": {"backend": backend}})
    try:
        if backend == StoreBackendType.MEMORY:
            logger.info("async_store_ready", extra={"event_fields": {"backend": backend}})
            yield InMemoryStore()
        else:
            async with AsyncSqliteStore.from_conn_string(_prepare_conn_string(settings)) as store:
                await store.setup()
                logger.info("async_store_ready", extra={"event_fields": {"backend": backend}})
                yield store
    finally:
        logger.debug("async_store_context_exited", extra={"event_fields": {"backend": backend}})


def create_checkpointer(settings: HarnessSettings) -> BaseCheckpointSaver[Any]:
    """根据已验证的配置创建立即可用的 LangGraph 检查点保存器（支持 Time Travel 与 Fork）。

    :param settings: 经校验的宿主配置对象。
    :return: 已执行 setup() 的 BaseCheckpointSaver 实例（InMemorySaver 或 SqliteSaver）。
    """
    target = settings.resolved_checkpoint_target
    if target == ":memory:":
        logger.debug("checkpointer_created", extra={"event_fields": {"backend": "memory"}})
        return InMemorySaver()
    logger.debug("checkpointer_opening", extra={"event_fields": {"backend": "sqlite"}})
    conn = sqlite3.connect(
        _prepare_checkpoint_conn_string(settings),
        check_same_thread=False,
    )
    try:
        checkpointer = SqliteSaver(conn)
        checkpointer.setup()
    except Exception:
        conn.close()
        raise
    logger.info("checkpointer_ready", extra={"event_fields": {"backend": "sqlite"}})
    return checkpointer


@contextmanager
def open_checkpointer(settings: HarnessSettings) -> Iterator[BaseCheckpointSaver[Any]]:
    """基于上下文管理器管理原生 SqliteSaver 检查点的同步生命周期。

    :param settings: 经校验的宿主配置对象。
    :return: 产生已完成 setup() 的 BaseCheckpointSaver 实例迭代器。
    """
    target = settings.resolved_checkpoint_target
    backend = "memory" if target == ":memory:" else "sqlite"
    logger.debug("checkpointer_opening", extra={"event_fields": {"backend": backend}})
    try:
        if backend == "memory":
            logger.debug("checkpointer_ready", extra={"event_fields": {"backend": backend}})
            yield InMemorySaver()
        else:
            with SqliteSaver.from_conn_string(_prepare_checkpoint_conn_string(settings)) as saver:
                saver.setup()
                logger.info("checkpointer_ready", extra={"event_fields": {"backend": backend}})
                yield saver
    finally:
        logger.debug("checkpointer_context_exited", extra={"event_fields": {"backend": backend}})


@asynccontextmanager
async def aopen_checkpointer(settings: HarnessSettings) -> AsyncIterator[BaseCheckpointSaver[Any]]:
    """基于异步上下文管理器管理原生 AsyncSqliteSaver 检查点的异步生命周期。

    :param settings: 经校验的宿主配置对象。
    :return: 产生已完成异步 setup() 的 BaseCheckpointSaver 实例异步迭代器。
    """
    target = settings.resolved_checkpoint_target
    backend = "memory" if target == ":memory:" else "sqlite"
    logger.debug("async_checkpointer_opening", extra={"event_fields": {"backend": backend}})
    try:
        if backend == "memory":
            logger.debug("async_checkpointer_ready", extra={"event_fields": {"backend": backend}})
            yield InMemorySaver()
        else:
            async with AsyncSqliteSaver.from_conn_string(
                _prepare_checkpoint_conn_string(settings)
            ) as saver:
                await saver.setup()
                logger.info(
                    "async_checkpointer_ready", extra={"event_fields": {"backend": backend}}
                )
                yield saver
    finally:
        logger.debug(
            "async_checkpointer_context_exited", extra={"event_fields": {"backend": backend}}
        )


def compose_backend(
    workspace_root: Path,
    store: BaseStore | None = None,
    *,
    memory_route: str = "/memories/",
    namespace_factory: Callable[[Runtime[Any]], tuple[str, ...]] = tenant_memory_namespace,
) -> BackendProtocol:
    """通过 CompositeBackend 将原生 FilesystemBackend 与 StoreBackend 进行组合挂载。

    :param workspace_root: 物理工作区根目录路径。
    :param store: 可选的 LangGraph 长期存储实例；若为 None 则退化为纯文件系统后端。
    :param memory_route: 长期记忆在虚拟文件系统中的路由前缀，默认为 '/memories/'。
    :param namespace_factory: 接受运行时对象并返回隔离命名空间元组的可调用工厂。
    :return: 组合后的虚拟文件系统后端 BackendProtocol 实例。
    """
    fs_backend = FilesystemBackend(root_dir=str(workspace_root), virtual_mode=True)
    if store is None:
        logger.debug("filesystem_backend_ready")
        return fs_backend

    # 使用 PurePosixPath 规范化虚拟记忆路由前缀（确保前后均保留单斜杠，如 '/memories/'）
    normalized_route = f"{PurePosixPath('/', memory_route.strip('/'))}/"
    backend = CompositeBackend(
        default=fs_backend,
        routes={normalized_route: StoreBackend(store=store, namespace=namespace_factory)},
    )
    logger.debug(
        "composite_backend_ready", extra={"event_fields": {"memory_route": normalized_route}}
    )
    return backend


__all__ = [
    "AsyncSqliteSaver",
    "AsyncSqliteStore",
    "SqliteSaver",
    "SqliteStore",
    "aopen_checkpointer",
    "aopen_store",
    "compose_backend",
    "create_checkpointer",
    "create_store",
    "open_checkpointer",
    "open_store",
]
