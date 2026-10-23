"""LangGraph checkpointer lifecycle factory."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from vertical_harness.config.settings import HarnessSettings

logger = logging.getLogger(__name__)


class CheckpointFactory:
    @staticmethod
    def _prepare_checkpoint_conn_string(settings: HarnessSettings) -> str:
        """确保基于文件的 Checkpointer SQLite 数据库父目录存在，并返回连接字符串。"""
        logger.debug("preparing_checkpoint_connection_string")
        target = settings.resolved_checkpoint_target
        if target != ":memory:":
            target.parent.mkdir(parents=True, exist_ok=True)
            return str(target)
        return target

    @classmethod
    def create_checkpointer(cls, settings: HarnessSettings) -> BaseCheckpointSaver[Any]:
        """根据已验证的配置创建立即可用的 LangGraph 检查点保存器（支持 Time Travel 与 Fork）。"""
        target = settings.resolved_checkpoint_target
        if target == ":memory:":
            logger.debug("checkpointer_created", extra={"event_fields": {"backend": "memory"}})
            return InMemorySaver()
        logger.debug("checkpointer_opening", extra={"event_fields": {"backend": "sqlite"}})
        conn = sqlite3.connect(
            cls._prepare_checkpoint_conn_string(settings),
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

    @classmethod
    @contextmanager
    def open_checkpointer(cls, settings: HarnessSettings) -> Iterator[BaseCheckpointSaver[Any]]:
        """基于上下文管理器管理原生 SqliteSaver 检查点的同步生命周期。"""
        target = settings.resolved_checkpoint_target
        backend = "memory" if target == ":memory:" else "sqlite"
        logger.debug("checkpointer_opening", extra={"event_fields": {"backend": backend}})
        try:
            if backend == "memory":
                logger.debug("checkpointer_ready", extra={"event_fields": {"backend": backend}})
                yield InMemorySaver()
            else:
                with SqliteSaver.from_conn_string(
                    cls._prepare_checkpoint_conn_string(settings)
                ) as saver:
                    saver.setup()
                    logger.info("checkpointer_ready", extra={"event_fields": {"backend": backend}})
                    yield saver
        finally:
            logger.debug(
                "checkpointer_context_exited", extra={"event_fields": {"backend": backend}}
            )

    @classmethod
    @asynccontextmanager
    async def aopen_checkpointer(
        cls, settings: HarnessSettings
    ) -> AsyncIterator[BaseCheckpointSaver[Any]]:
        """基于异步上下文管理器管理原生 AsyncSqliteSaver 检查点的异步生命周期。"""
        target = settings.resolved_checkpoint_target
        backend = "memory" if target == ":memory:" else "sqlite"
        logger.debug("async_checkpointer_opening", extra={"event_fields": {"backend": backend}})
        try:
            if backend == "memory":
                logger.debug(
                    "async_checkpointer_ready", extra={"event_fields": {"backend": backend}}
                )
                yield InMemorySaver()
            else:
                async with AsyncSqliteSaver.from_conn_string(
                    cls._prepare_checkpoint_conn_string(settings)
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
