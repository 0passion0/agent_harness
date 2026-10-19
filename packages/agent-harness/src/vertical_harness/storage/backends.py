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
    :type settings: vertical_harness.config.settings.HarnessSettings
    :return: SQLite 连接字符串或数据库物理文件路径字符串。
    :rtype: str
    :author: tongliga
    :date: 2026-10-19
    """
    logger.debug("preparing_store_connection_string")
    # 第一步：获取解析后的存储目标路径
    target = settings.resolved_store_target
    # 第二步：若为物理文件路径，递归确保父目录已创建
    if isinstance(target, Path):
        target.parent.mkdir(parents=True, exist_ok=True)
        return str(target)
    return target


def _prepare_checkpoint_conn_string(settings: HarnessSettings) -> str:
    """确保基于文件的 Checkpointer SQLite 数据库父目录存在，并返回连接字符串。

    :param settings: 经校验的宿主配置对象。
    :type settings: vertical_harness.config.settings.HarnessSettings
    :return: Checkpointer SQLite 连接字符串或数据库物理文件路径字符串。
    :rtype: str
    :author: tongliga
    :date: 2026-10-19
    """
    logger.debug("preparing_checkpoint_connection_string")
    # 第一步：获取解析后的检查点存储目标路径
    target = settings.resolved_checkpoint_target
    # 第二步：若为物理文件路径，递归确保父目录已创建
    if isinstance(target, Path):
        target.parent.mkdir(parents=True, exist_ok=True)
        return str(target)
    return target


def create_store(settings: HarnessSettings) -> BaseStore:
    """根据已验证的配置创建立即可用的原生 LangGraph BaseStore 存储实例。

    :param settings: 经校验的宿主配置对象。
    :type settings: vertical_harness.config.settings.HarnessSettings
    :return: 已执行初始化 setup() 的 BaseStore 存储实例（InMemoryStore 或 SqliteStore）。
    :rtype: langgraph.store.base.BaseStore
    :author: tongliga
    :date: 2026-10-19
    """
    # 第一步：纯内存模式下直接返回 InMemoryStore 实例
    if settings.store_backend == StoreBackendType.MEMORY:
        logger.debug("store_created", extra={"event_fields": {"backend": "memory"}})
        return InMemoryStore()

    # 第二步：SQLite 模式下建立跨线程共享连接并执行表结构 setup()
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
    # 第三步：记录就绪日志并返回实例
    logger.info("store_ready", extra={"event_fields": {"backend": "sqlite"}})
    return store


@contextmanager
def open_store(settings: HarnessSettings) -> Iterator[BaseStore]:
    """基于上下文管理器管理原生 SqliteStore 的同步生命周期。

    :param settings: 经校验的宿主配置对象。
    :type settings: vertical_harness.config.settings.HarnessSettings
    :return: 产生已完成 setup() 的 BaseStore 存储实例迭代器。
    :rtype: collections.abc.Iterator[langgraph.store.base.BaseStore]
    :author: tongliga
    :date: 2026-10-19
    """
    backend = settings.store_backend
    logger.debug("store_opening", extra={"event_fields": {"backend": backend}})
    try:
        # 第一步：根据后端配置分流内存或文件模式
        if backend == StoreBackendType.MEMORY:
            logger.info("store_ready", extra={"event_fields": {"backend": backend}})
            yield InMemoryStore()
        else:
            # 第二步：通过上下文管理器安全打开并初始化表结构
            with SqliteStore.from_conn_string(_prepare_conn_string(settings)) as store:
                store.setup()
                logger.info("store_ready", extra={"event_fields": {"backend": backend}})
                yield store
    finally:
        # 第三步：退出上下文时记录诊断日志
        logger.debug("store_context_exited", extra={"event_fields": {"backend": backend}})


@asynccontextmanager
async def aopen_store(settings: HarnessSettings) -> AsyncIterator[BaseStore]:
    """基于异步上下文管理器管理原生 AsyncSqliteStore 的异步生命周期。

    :param settings: 经校验的宿主配置对象。
    :type settings: vertical_harness.config.settings.HarnessSettings
    :return: 产生已完成异步 setup() 的 BaseStore 存储实例异步迭代器。
    :rtype: collections.abc.AsyncIterator[langgraph.store.base.BaseStore]
    :author: tongliga
    :date: 2026-10-19
    """
    backend = settings.store_backend
    logger.debug("async_store_opening", extra={"event_fields": {"backend": backend}})
    try:
        # 第一步：根据后端配置分流内存或异步 SQLite 模式
        if backend == StoreBackendType.MEMORY:
            logger.info("async_store_ready", extra={"event_fields": {"backend": backend}})
            yield InMemoryStore()
        else:
            # 第二步：异步打开 AsyncSqliteStore 并执行异步 setup()
            async with AsyncSqliteStore.from_conn_string(_prepare_conn_string(settings)) as store:
                await store.setup()
                logger.info("async_store_ready", extra={"event_fields": {"backend": backend}})
                yield store
    finally:
        # 第三步：退出异步上下文时记录诊断日志
        logger.debug("async_store_context_exited", extra={"event_fields": {"backend": backend}})


def create_checkpointer(settings: HarnessSettings) -> BaseCheckpointSaver[Any]:
    """根据已验证的配置创建立即可用的 LangGraph 检查点保存器（支持 Time Travel 与 Fork）。

    :param settings: 经校验的宿主配置对象。
    :type settings: vertical_harness.config.settings.HarnessSettings
    :return: 已执行 setup() 的 BaseCheckpointSaver 实例（InMemorySaver 或 SqliteSaver）。
    :rtype: langgraph.checkpoint.base.BaseCheckpointSaver[typing.Any]
    :author: tongliga
    :date: 2026-10-19
    """
    # 第一步：内存目标下直接返回 InMemorySaver 实例
    target = settings.resolved_checkpoint_target
    if target == ":memory:":
        logger.debug("checkpointer_created", extra={"event_fields": {"backend": "memory"}})
        return InMemorySaver()

    # 第二步：SQLite 模式下建立跨线程连接并执行检查点表初始化 setup()
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
    # 第三步：记录就绪日志并返回检查点保存器
    logger.info("checkpointer_ready", extra={"event_fields": {"backend": "sqlite"}})
    return checkpointer


@contextmanager
def open_checkpointer(settings: HarnessSettings) -> Iterator[BaseCheckpointSaver[Any]]:
    """基于上下文管理器管理原生 SqliteSaver 检查点的同步生命周期。

    :param settings: 经校验的宿主配置对象。
    :type settings: vertical_harness.config.settings.HarnessSettings
    :return: 产生已完成 setup() 的 BaseCheckpointSaver 实例迭代器。
    :rtype: collections.abc.Iterator[langgraph.checkpoint.base.BaseCheckpointSaver[typing.Any]]
    :author: tongliga
    :date: 2026-10-19
    """
    target = settings.resolved_checkpoint_target
    backend = "memory" if target == ":memory:" else "sqlite"
    logger.debug("checkpointer_opening", extra={"event_fields": {"backend": backend}})
    try:
        # 第一步：根据目标类型分流内存检查点或 SQLite 检查点
        if backend == "memory":
            logger.debug("checkpointer_ready", extra={"event_fields": {"backend": backend}})
            yield InMemorySaver()
        else:
            # 第二步：使用 SqliteSaver 上下文管理器安全打开并 setup()
            with SqliteSaver.from_conn_string(_prepare_checkpoint_conn_string(settings)) as saver:
                saver.setup()
                logger.info("checkpointer_ready", extra={"event_fields": {"backend": backend}})
                yield saver
    finally:
        # 第三步：退出上下文时记录诊断日志
        logger.debug("checkpointer_context_exited", extra={"event_fields": {"backend": backend}})


@asynccontextmanager
async def aopen_checkpointer(settings: HarnessSettings) -> AsyncIterator[BaseCheckpointSaver[Any]]:
    """基于异步上下文管理器管理原生 AsyncSqliteSaver 检查点的异步生命周期。

    :param settings: 经校验的宿主配置对象。
    :type settings: vertical_harness.config.settings.HarnessSettings
    :return: 产生已完成异步 setup() 的 BaseCheckpointSaver 实例异步迭代器。
    :rtype: collections.abc.AsyncIterator[langgraph.checkpoint.base.BaseCheckpointSaver[typing.Any]]
    :author: tongliga
    :date: 2026-10-19
    """
    target = settings.resolved_checkpoint_target
    backend = "memory" if target == ":memory:" else "sqlite"
    logger.debug("async_checkpointer_opening", extra={"event_fields": {"backend": backend}})
    try:
        # 第一步：根据目标类型分流内存或异步 SQLite 检查点
        if backend == "memory":
            logger.debug("async_checkpointer_ready", extra={"event_fields": {"backend": backend}})
            yield InMemorySaver()
        else:
            # 第二步：异步打开 AsyncSqliteSaver 并执行异步 setup()
            async with AsyncSqliteSaver.from_conn_string(
                _prepare_checkpoint_conn_string(settings)
            ) as saver:
                await saver.setup()
                logger.info(
                    "async_checkpointer_ready", extra={"event_fields": {"backend": backend}}
                )
                yield saver
    finally:
        # 第三步：退出异步上下文时记录诊断日志
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
    :type workspace_root: pathlib.Path
    :param store: 可选的 LangGraph 长期存储实例；若为 None 则退化为纯文件系统后端。
    :type store: langgraph.store.base.BaseStore | None
    :param memory_route: 长期记忆在虚拟文件系统中的路由前缀，默认为 '/memories/'。
    :type memory_route: str
    :param namespace_factory: 接受运行时对象并返回隔离命名空间元组的可调用工厂。
    :type namespace_factory: collections.abc.Callable[[langgraph.runtime.Runtime[typing.Any]], tuple[str, ...]]
    :return: 组合后的虚拟文件系统后端 BackendProtocol 实例。
    :rtype: deepagents.backends.protocol.BackendProtocol
    :author: tongliga
    :date: 2026-10-19
    """
    # 第一步：实例化底层工作区物理文件系统后端（启用虚拟路径模式）
    fs_backend = FilesystemBackend(root_dir=str(workspace_root), virtual_mode=True)
    if store is None:
        logger.debug("filesystem_backend_ready")
        return fs_backend

    # 第二步：规范化 POSIX 路由前缀（前后带斜杠，如 '/memories/'）
    normalized_route = f"{PurePosixPath('/', memory_route.strip('/'))}/"
    # 第三步：装配默认文件系统与长期记忆路由的 CompositeBackend 实例并返回
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
