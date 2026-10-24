"""LangGraph checkpointer lifecycle factory.

本模块提供 `CheckpointFactory` 检查点保存器工厂：
1. 统一适配持久化 SQLite 后端（`SqliteSaver` / `AsyncSqliteSaver`）与快速纯内存测试后端（`InMemorySaver`）；
2. 自动数据库建表与迁移（`setup()` / `await setup()`）；
3. 提供同步与异步上下文管理器，确保 SQLite 数据库文件连接与 WAL 缓冲区在运行结束时安全关闭与落盘。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from vertical_harness.models import HarnessSettings

# 模块级日志记录器，记录检查点数据库打开与就绪状态
logger = logging.getLogger(__name__)


class CheckpointFactory:
    """LangGraph 检查点保存器生命周期工厂，支持内存与 SQLite 后端的同步与异步上下文创建。

    纯静态/类方法工厂，负责解析宿主配置并初始化对应的 Checkpointer。
    """

    @staticmethod
    def _prepare_checkpoint_conn_string(settings: HarnessSettings) -> str:
        """确保基于文件的 Checkpointer SQLite 数据库父目录存在，并返回连接字符串。

        Args:
            settings (HarnessSettings): 宿主配置对象，提供检查点路径信息。

        Returns:
            str: 格式化后的 SQLite 数据库物理绝对路径字符串或 `":memory:"` 内存标识符。
        """
        logger.debug("preparing_checkpoint_connection_string")
        target = settings.resolved_checkpoint_target
        if target != ":memory:":
            target.parent.mkdir(parents=True, exist_ok=True)
            return str(target)
        return target

    @classmethod
    @contextmanager
    def open_checkpointer(cls, settings: HarnessSettings) -> Iterator[BaseCheckpointSaver[Any]]:
        """基于上下文管理器管理原生 SqliteSaver 检查点的同步生命周期。

        Args:
            settings (HarnessSettings): 宿主配置实例。

        Yields:
            BaseCheckpointSaver[Any]: 同步检查点保存器实例。
        """
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
        """基于异步上下文管理器管理原生 AsyncSqliteSaver 检查点的异步生命周期。

        Args:
            settings (HarnessSettings): 宿主配置实例。

        Yields:
            BaseCheckpointSaver[Any]: 异步检查点保存器实例。
        """
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
