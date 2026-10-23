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
        """确保基于文件的 SQLite 数据库父目录存在，并返回连接字符串。"""
        logger.debug("preparing_store_connection_string")
        target = settings.resolved_store_target
        if target != ":memory:":
            target.parent.mkdir(parents=True, exist_ok=True)
            return str(target)
        return target

    @classmethod
    def create_store(cls, settings: HarnessSettings) -> BaseStore:
        """根据已验证的配置创建立即可用的原生 LangGraph BaseStore 存储实例。"""
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
        """基于上下文管理器管理原生 SqliteStore 的同步生命周期。"""
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
        """基于异步上下文管理器管理原生 AsyncSqliteStore 的异步生命周期。"""
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
