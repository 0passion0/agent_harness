"""解耦的 Store 与 Backend 组合模块：基于原生 LangGraph SQLite 实现数据持久化与虚拟文件系统挂载。"""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from deepagents.backends import CompositeBackend, FilesystemBackend, StoreBackend
from deepagents.backends.protocol import BackendProtocol
from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore
from langgraph.store.sqlite import AsyncSqliteStore, SqliteStore

from vertical_harness.config.settings import StoreBackendType
from vertical_harness.storage.namespace import tenant_memory_namespace

if TYPE_CHECKING:
    from langgraph.runtime import Runtime

    from vertical_harness.config.settings import HarnessSettings


def _prepare_conn_string(settings: HarnessSettings) -> str:
    """确保基于文件的 SQLite 数据库父目录存在，并返回连接字符串。

    :param settings: 经校验的宿主配置对象。
    :return: SQLite 连接字符串或数据库物理文件路径字符串。
    """
    target = settings.resolved_store_target
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
    """基于上下文管理器管理原生 SqliteStore 的同步生命周期。

    :param settings: 经校验的宿主配置对象。
    :return: 产生已完成 setup() 的 BaseStore 存储实例迭代器。
    """
    if settings.store_backend == StoreBackendType.MEMORY:
        yield InMemoryStore()
        return
    with SqliteStore.from_conn_string(_prepare_conn_string(settings)) as store:
        store.setup()
        yield store


@asynccontextmanager
async def aopen_store(settings: HarnessSettings) -> AsyncIterator[BaseStore]:
    """基于异步上下文管理器管理原生 AsyncSqliteStore 的异步生命周期。

    :param settings: 经校验的宿主配置对象。
    :return: 产生已完成异步 setup() 的 BaseStore 存储实例异步迭代器。
    """
    if settings.store_backend == StoreBackendType.MEMORY:
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
    """通过 CompositeBackend 将原生 FilesystemBackend 与 StoreBackend 进行组合挂载。

    :param workspace_root: 物理工作区根目录路径。
    :param store: 可选的 LangGraph 长期存储实例；若为 None 则退化为纯文件系统后端。
    :param memory_route: 长期记忆在虚拟文件系统中的路由前缀，默认为 '/memories/'。
    :param namespace_factory: 接受运行时对象并返回隔离命名空间元组的可调用工厂。
    :return: 组合后的虚拟文件系统后端 BackendProtocol 实例。
    """
    fs_backend = FilesystemBackend(root_dir=str(workspace_root), virtual_mode=True)
    if store is None:
        return fs_backend

    # 使用 PurePosixPath 规范化虚拟记忆路由前缀（确保前后均保留单斜杠，如 '/memories/'）
    normalized_route = f"{PurePosixPath('/', memory_route.strip('/'))}/"
    return CompositeBackend(
        default=fs_backend,
        routes={normalized_route: StoreBackend(store=store, namespace=namespace_factory)},
    )


__all__ = [
    "AsyncSqliteStore",
    "SqliteStore",
    "aopen_store",
    "compose_backend",
    "create_store",
    "open_store",
]
