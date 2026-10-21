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
        """确保基于文件的 Checkpointer SQLite 数据库父目录存在，并返回连接字符串。

        :param settings: 经校验的宿主配置对象。
        :type settings: vertical_harness.config.settings.HarnessSettings
        :return: Checkpointer SQLite 连接字符串或数据库物理文件路径字符串。
        :rtype: str
        :author: tongliga
        :date: 2026-10-21
        """
        logger.debug("preparing_checkpoint_connection_string")
        # 第一步：获取解析后的检查点存储目标路径
        target = settings.resolved_checkpoint_target
        # 第二步：若为物理文件路径，递归确保父目录已创建
        if target != ":memory:":
            target.parent.mkdir(parents=True, exist_ok=True)
            return str(target)
        return target

    @classmethod
    def create_checkpointer(cls, settings: HarnessSettings) -> BaseCheckpointSaver[Any]:
        """根据已验证的配置创建立即可用的 LangGraph 检查点保存器（支持 Time Travel 与 Fork）。

        :param settings: 经校验的宿主配置对象。
        :type settings: vertical_harness.config.settings.HarnessSettings
        :return: 已执行 setup() 的 BaseCheckpointSaver 实例（InMemorySaver 或 SqliteSaver）。
        :rtype: langgraph.checkpoint.base.BaseCheckpointSaver[typing.Any]
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：内存目标下直接返回 InMemorySaver 实例
        target = settings.resolved_checkpoint_target
        if target == ":memory:":
            logger.debug("checkpointer_created", extra={"event_fields": {"backend": "memory"}})
            return InMemorySaver()

        # 第二步：SQLite 模式下建立跨线程连接并执行检查点表初始化 setup()
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
        # 第三步：记录就绪日志并返回检查点保存器
        logger.info("checkpointer_ready", extra={"event_fields": {"backend": "sqlite"}})
        return checkpointer

    @classmethod
    @contextmanager
    def open_checkpointer(cls, settings: HarnessSettings) -> Iterator[BaseCheckpointSaver[Any]]:
        """基于上下文管理器管理原生 SqliteSaver 检查点的同步生命周期。

        :param settings: 经校验的宿主配置对象。
        :type settings: vertical_harness.config.settings.HarnessSettings
        :return: 产生已完成 setup() 的 BaseCheckpointSaver 实例迭代器。
        :rtype: collections.abc.Iterator[langgraph.checkpoint.base.BaseCheckpointSaver[typing.Any]]
        :author: tongliga
        :date: 2026-10-21
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
                with SqliteSaver.from_conn_string(
                        cls._prepare_checkpoint_conn_string(settings)
                ) as saver:
                    saver.setup()
                    logger.info("checkpointer_ready", extra={"event_fields": {"backend": backend}})
                    yield saver
        finally:
            # 第三步：退出上下文时记录诊断日志
            logger.debug(
                "checkpointer_context_exited", extra={"event_fields": {"backend": backend}}
            )

    @classmethod
    @asynccontextmanager
    async def aopen_checkpointer(
            cls, settings: HarnessSettings
    ) -> AsyncIterator[BaseCheckpointSaver[Any]]:
        """基于异步上下文管理器管理原生 AsyncSqliteSaver 检查点的异步生命周期。

        :param settings: 经校验的宿主配置对象。
        :type settings: vertical_harness.config.settings.HarnessSettings
        :return: 产生已完成异步 setup() 的 BaseCheckpointSaver 实例异步迭代器。
        :rtype: collections.abc.AsyncIterator[langgraph.checkpoint.base.BaseCheckpointSaver[typing.Any]]
        :author: tongliga
        :date: 2026-10-21
        """
        target = settings.resolved_checkpoint_target
        backend = "memory" if target == ":memory:" else "sqlite"
        logger.debug("async_checkpointer_opening", extra={"event_fields": {"backend": backend}})
        try:
            # 第一步：根据目标类型分流内存或异步 SQLite 检查点
            if backend == "memory":
                logger.debug(
                    "async_checkpointer_ready", extra={"event_fields": {"backend": backend}}
                )
                yield InMemorySaver()
            else:
                # 第二步：异步打开 AsyncSqliteSaver 并执行异步 setup()
                async with AsyncSqliteSaver.from_conn_string(
                        cls._prepare_checkpoint_conn_string(settings)
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
