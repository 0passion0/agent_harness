"""解耦的 Store 与 Backend 组合模块：基于原生 LangGraph SQLite 实现数据持久化与虚拟文件系统挂载。"""

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

# LangGraph 原生 SqliteStore 使用 '.' 拼接命名空间分段，因此分段内部严禁出现 '.' 字符；
# 此处正则匹配除英文字母、数字、减号、下划线、@、+、:、~ 之外的所有不安全字符。
_UNSAFE_NS_CHARS = re.compile(r"[^A-Za-z0-9\-_@+:~]")


def _sanitize_segment(value: str | None, fallback: str = "default") -> str:
    """将运行时标识符规范化为 LangGraph 与 StoreBackend 安全合法的命名空间标签。

    :param value: 待清洗的原始命名空间分段字符串，可能为 None 或包含特殊字符。
    :param fallback: 当输入为 None 或清洗后为空字符串时的回退默认值。
    :return: 仅包含安全字符的规范化命名空间分段字符串。
    """
    # 若输入为空或空字符串，直接返回回退默认值
    if not value:
        return fallback
    # 将所有非安全字符（如 '.'、空格、斜杠等）统一替换为下划线 '_'
    cleaned = _UNSAFE_NS_CHARS.sub("_", value.strip())
    # 清洗后若为空串则回退
    return cleaned or fallback


def tenant_memory_namespace(
    runtime: Runtime[RunContext] | None = None,
    *,
    bucket: str = "memories",
) -> tuple[str, ...]:
    """从 LangGraph Runtime 运行时上下文中派生出租户和用户隔离的命名空间元组。

    :param runtime: LangGraph 运行时对象，其 context 属性包含当前租户与用户标识。
    :param bucket: 存储分类桶标识，默认为 'memories'。
    :return: 由清洗后的 (tenant_id, user_id, bucket) 组成的隔离命名空间三元组。
    """
    # 从 runtime 中安全获取注入的 RunContext 对象
    context = getattr(runtime, "context", None) if runtime is not None else None
    # 分别清洗租户 ID、用户 ID 和存储桶名称，组合成多租户隔离的命名空间元组
    return (
        _sanitize_segment(getattr(context, "tenant_id", None), "default"),
        _sanitize_segment(getattr(context, "user_id", None), "default"),
        _sanitize_segment(bucket, "memories"),
    )


def _prepare_conn_string(settings: HarnessSettings) -> str:
    """确保基于文件的 SQLite 数据库父目录存在，并返回连接字符串。

    :param settings: 经校验的宿主配置对象。
    :return: SQLite 连接字符串或数据库物理文件路径字符串。
    """
    # 获取解析后的数据库目标路径或 ':memory:'
    target = settings.resolved_store_target
    # 若目标为物理文件路径，递归创建父级目录以防数据库连接失败
    if isinstance(target, Path):
        target.parent.mkdir(parents=True, exist_ok=True)
        return str(target)
    # 内存数据库标识直接返回
    return target


def create_store(settings: HarnessSettings) -> BaseStore:
    """根据已验证的配置创建立即可用的原生 LangGraph BaseStore 存储实例。

    :param settings: 经校验的宿主配置对象。
    :return: 已执行初始化 setup() 的 BaseStore 存储实例（InMemoryStore 或 SqliteStore）。
    """
    # 若配置为内存存储，直接返回 InMemoryStore 实例
    if settings.store_backend == "memory":
        return InMemoryStore()
    # 建立 SQLite 连接，禁用同线程限制以支持多线程/异步场景，开启 autocommit (isolation_level=None)
    conn = sqlite3.connect(
        _prepare_conn_string(settings),
        check_same_thread=False,
        isolation_level=None,
    )
    # 基于原生连接构造 SqliteStore 实例
    store = SqliteStore(conn)
    # 执行建表及初始化操作
    store.setup()
    return store


@contextmanager
def open_store(settings: HarnessSettings) -> Iterator[BaseStore]:
    """基于上下文管理器管理原生 SqliteStore 的同步生命周期。

    :param settings: 经校验的宿主配置对象。
    :return: 产生已完成 setup() 的 BaseStore 存储实例迭代器。
    """
    # 内存存储直接让步实例
    if settings.store_backend == "memory":
        yield InMemoryStore()
        return
    # 使用 from_conn_string 工厂方法安全管理 SQLite 连接生命周期
    with SqliteStore.from_conn_string(_prepare_conn_string(settings)) as store:
        # 执行表结构初始化
        store.setup()
        yield store


@asynccontextmanager
async def aopen_store(settings: HarnessSettings) -> AsyncIterator[BaseStore]:
    """基于异步上下文管理器管理原生 AsyncSqliteStore 的异步生命周期。

    :param settings: 经校验的宿主配置对象。
    :return: 产生已完成异步 setup() 的 BaseStore 存储实例异步迭代器。
    """
    # 内存存储直接让步实例
    if settings.store_backend == "memory":
        yield InMemoryStore()
        return
    # 使用异步工厂方法管理 AsyncSqliteStore 生命周期
    async with AsyncSqliteStore.from_conn_string(_prepare_conn_string(settings)) as store:
        # 异步执行建表及环境初始化
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
    # 初始化物理工作区对应的虚拟文件系统后端（开启虚拟路径模式）
    fs_backend = FilesystemBackend(root_dir=str(workspace_root), virtual_mode=True)
    # 若未提供 store，直接返回纯文件系统后端
    if store is None:
        return fs_backend

    # 规范化记忆路由前缀格式（前后均保留单斜杠，如 '/memories/'）
    route = "/" + memory_route.strip("/") + "/"
    # 构造组合后端：默认请求映射至物理工作区文件系统，特定前缀路由重定向至 StoreBackend 持久化存储
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

