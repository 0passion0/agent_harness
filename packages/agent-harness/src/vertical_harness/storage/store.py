"""LangGraph store lifecycle factory.

本模块提供 `StoreFactory` 全局键值状态存储器工厂：
1. 统一管理 LangGraph 跨会话全局存储 `BaseStore` 的初始化；
2. 支持内存型后端 `InMemoryStore` 与持久化 SQLite 后端 `SqliteStore` / `AsyncSqliteStore`；
3. 自动配置自动提交模式（`isolation_level=None`）与表结构初始化（`setup()`），提供安全的资源生命周期闭环。
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager

from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore
from langgraph.store.sqlite import AsyncSqliteStore, SqliteStore

from vertical_harness.config.settings import HarnessSettings
from vertical_harness.config.store_backend import StoreBackendType

# 模块级日志记录器，记录状态存储打开与就绪状态
logger = logging.getLogger(__name__)


class StoreFactory:
    """LangGraph BaseStore 全局状态存储生命周期工厂，支持纯内存与 SQLite 持久化后端。

    纯静态/类方法工厂，供服务层统一申请键值存储连接。
    """

    @staticmethod
    def _prepare_conn_string(settings: HarnessSettings) -> str:
        """确保基于文件的 SQLite 数据库父目录存在，并返回连接字符串。

        Args:
            settings (HarnessSettings): 宿主配置实例。

        Returns:
            str: 格式化后的 SQLite 数据库物理绝对路径字符串或 `":memory:"` 内存标识符。
        """
        logger.debug("preparing_store_connection_string")
        target = settings.resolved_store_target
        if target != ":memory:":
            target.parent.mkdir(parents=True, exist_ok=True)
            return str(target)
        return target

    @classmethod
    def create_store(cls, settings: HarnessSettings) -> BaseStore:
        """根据已验证的配置创建立即可用的原生 LangGraph BaseStore 存储实例。

        Args:
            settings (HarnessSettings): 宿主全局配置对象。

        Returns:
            BaseStore: 包含表结构的初始化就绪 BaseStore 实例。

        Raises:
            Exception: 当 SQLite 初始化异常时关闭连接并抛出。
        """
        if settings.store_backend == StoreBackendType.MEMORY:
            logger.debug("store_created", extra={"event_fields": {"backend": "memory"}})
            return InMemoryStore()
        logger.debug("store_opening", extra={"event_fields": {"backend": "sqlite"}})
        conn = sqlite3.connect(
            cls._prepare_conn_string(settings),
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

    @classmethod
    @contextmanager
    def open_store(cls, settings: HarnessSettings) -> Iterator[BaseStore]:
        """基于上下文管理器管理原生 SqliteStore 的同步生命周期。

        Args:
            settings (HarnessSettings): 宿主配置对象。

        Yields:
            BaseStore: 同步存储后端实例。
        """
        backend = settings.store_backend
        logger.debug("store_opening", extra={"event_fields": {"backend": backend}})
        try:
            if backend == StoreBackendType.MEMORY:
                logger.info("store_ready", extra={"event_fields": {"backend": backend}})
                yield InMemoryStore()
            else:
                with SqliteStore.from_conn_string(cls._prepare_conn_string(settings)) as store:
                    store.setup()
                    logger.info("store_ready", extra={"event_fields": {"backend": backend}})
                    yield store
        finally:
            logger.debug("store_context_exited", extra={"event_fields": {"backend": backend}})

    @classmethod
    @asynccontextmanager
    async def aopen_store(cls, settings: HarnessSettings) -> AsyncIterator[BaseStore]:
        """基于异步上下文管理器管理原生 AsyncSqliteStore 的异步生命周期。

        Args:
            settings (HarnessSettings): 宿主配置对象。

        Yields:
            BaseStore: 异步存储后端实例。
        """
        backend = settings.store_backend
        logger.debug("async_store_opening", extra={"event_fields": {"backend": backend}})
        try:
            if backend == StoreBackendType.MEMORY:
                logger.info("async_store_ready", extra={"event_fields": {"backend": backend}})
                yield InMemoryStore()
            else:
                async with AsyncSqliteStore.from_conn_string(
                    cls._prepare_conn_string(settings)
                ) as store:
                    await store.setup()
                    logger.info("async_store_ready", extra={"event_fields": {"backend": backend}})
                    yield store
        finally:
            logger.debug("async_store_context_exited", extra={"event_fields": {"backend": backend}})
