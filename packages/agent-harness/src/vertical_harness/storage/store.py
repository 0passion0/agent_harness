"""LangGraph store lifecycle factory."""

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

logger = logging.getLogger(__name__)


class StoreFactory:
    @staticmethod
    def _prepare_conn_string(settings: HarnessSettings) -> str:
        """确保基于文件的 SQLite 数据库父目录存在，并返回连接字符串。

        :param settings: 经校验的宿主配置对象。
        :type settings: vertical_harness.config.settings.HarnessSettings
        :return: SQLite 连接字符串或数据库物理文件路径字符串。
        :rtype: str
        :author: tongliga
        :date: 2026-10-21
        """
        logger.debug("preparing_store_connection_string")
        # 第一步：获取解析后的存储目标路径
        target = settings.resolved_store_target
        # 第二步：若为物理文件路径，递归确保父目录已创建
        if target != ":memory:":
            target.parent.mkdir(parents=True, exist_ok=True)
            return str(target)
        return target

    @classmethod
    def create_store(cls, settings: HarnessSettings) -> BaseStore:
        """根据已验证的配置创建立即可用的原生 LangGraph BaseStore 存储实例。

        :param settings: 经校验的宿主配置对象。
        :type settings: vertical_harness.config.settings.HarnessSettings
        :return: 已执行初始化 setup() 的 BaseStore 存储实例（InMemoryStore 或 SqliteStore）。
        :rtype: langgraph.store.base.BaseStore
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：纯内存模式下直接返回 InMemoryStore 实例
        if settings.store_backend == StoreBackendType.MEMORY:
            logger.debug("store_created", extra={"event_fields": {"backend": "memory"}})
            return InMemoryStore()

        # 第二步：SQLite 模式下建立跨线程共享连接并执行表结构 setup()
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
        # 第三步：记录就绪日志并返回实例
        logger.info("store_ready", extra={"event_fields": {"backend": "sqlite"}})
        return store

    @classmethod
    @contextmanager
    def open_store(cls, settings: HarnessSettings) -> Iterator[BaseStore]:
        """基于上下文管理器管理原生 SqliteStore 的同步生命周期。

        :param settings: 经校验的宿主配置对象。
        :type settings: vertical_harness.config.settings.HarnessSettings
        :return: 产生已完成 setup() 的 BaseStore 存储实例迭代器。
        :rtype: collections.abc.Iterator[langgraph.store.base.BaseStore]
        :author: tongliga
        :date: 2026-10-21
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
                with SqliteStore.from_conn_string(cls._prepare_conn_string(settings)) as store:
                    store.setup()
                    logger.info("store_ready", extra={"event_fields": {"backend": backend}})
                    yield store
        finally:
            # 第三步：退出上下文时记录诊断日志
            logger.debug("store_context_exited", extra={"event_fields": {"backend": backend}})

    @classmethod
    @asynccontextmanager
    async def aopen_store(cls, settings: HarnessSettings) -> AsyncIterator[BaseStore]:
        """基于异步上下文管理器管理原生 AsyncSqliteStore 的异步生命周期。

        :param settings: 经校验的宿主配置对象。
        :type settings: vertical_harness.config.settings.HarnessSettings
        :return: 产生已完成异步 setup() 的 BaseStore 存储实例异步迭代器。
        :rtype: collections.abc.AsyncIterator[langgraph.store.base.BaseStore]
        :author: tongliga
        :date: 2026-10-21
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
                async with AsyncSqliteStore.from_conn_string(
                        cls._prepare_conn_string(settings)
                ) as store:
                    await store.setup()
                    logger.info("async_store_ready", extra={"event_fields": {"backend": backend}})
                    yield store
        finally:
            # 第三步：退出异步上下文时记录诊断日志
            logger.debug("async_store_context_exited", extra={"event_fields": {"backend": backend}})
