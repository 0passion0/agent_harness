"""解耦的 Store 与 Backend 组合模块：基于原生 LangGraph SQLite 实现数据持久化与虚拟文件系统挂载。"""

from __future__ import annotations

import logging
import os
import re
import shutil
import sqlite3
import tempfile
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from itertools import islice
from pathlib import Path
from typing import Any

from deepagents.backends import FilesystemBackend
from deepagents.backends.protocol import EditResult, FileData, ReadResult, WriteResult
from deepagents.backends.utils import slice_read_response
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore
from langgraph.store.sqlite import AsyncSqliteStore, SqliteStore

from vertical_harness.config.settings import HarnessSettings, StoreBackendType
from vertical_harness.desktop_attachments import extract_text

logger = logging.getLogger(__name__)


class DocumentFilesystemBackend(FilesystemBackend):
    """Paginated UTF-8 and document reads with atomic text edits."""

    _TEXT_DOCUMENTS = frozenset({".pdf", ".docx", ".xlsx", ".xlsm", ".pptx"})
    _SCRIPT_DOCUMENTS = frozenset(
        {".doc", ".ppt", ".pptm", ".docm", ".xls", ".xlsb", ".odt", ".ods", ".odp"}
    )

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self._document_cache: tuple[tuple[Any, ...], str] | None = None

    def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        """Read a window; extract and cache only the most recent document."""
        offset, limit = max(0, offset), max(0, limit)
        if not limit:
            return ReadResult(
                file_data=FileData(content="", encoding="utf-8"), no_lines_requested=True
            )
        try:
            path = self._resolve_path(file_path)
            suffix = path.suffix.lower()
            if suffix in self._SCRIPT_DOCUMENTS:
                return ReadResult(
                    error=f"{suffix} requires a suitable Python library; read_file does not parse it."
                )
            if suffix in self._TEXT_DOCUMENTS:
                stat = path.stat()
                key = (str(path), stat.st_size, stat.st_mtime_ns)
                if self._document_cache is None or self._document_cache[0] != key:
                    self._document_cache = (key, extract_text(path))
                return slice_read_response(
                    FileData(content=self._document_cache[1], encoding="utf-8"), offset, limit
                )
            if suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
                return super().read(file_path, offset, limit)
            with path.open(encoding="utf-8-sig") as stream:
                window = list(islice(stream, offset, offset + limit + 1))
            if not window and offset:
                return ReadResult(error=f"Line offset {offset} exceeds file length")
            more = len(window) > limit
            content = "".join(window[:limit])
            if "\0" in content:
                return ReadResult(error="Binary file: use a suitable Python library.")
            end = offset + min(limit, len(window))
            return ReadResult(
                file_data=FileData(content=content, encoding="utf-8"),
                start_line=offset + 1 if window else None,
                end_line=end if window else None,
                next_offset=end if more else None,
                total_lines=None if more else end,
            )
        except (OSError, ValueError) as exc:
            return ReadResult(error=f"Cannot read file ({type(exc).__name__}).")

    def _text_path(self, file_path: str) -> Path:
        """Apply native workspace checks and reject document/binary text edits."""
        path = self._resolve_path(file_path)
        if path.suffix.lower() in self._TEXT_DOCUMENTS | self._SCRIPT_DOCUMENTS:
            raise ValueError("Office/PDF files must be edited with a suitable Python library")
        if path.exists():
            data = path.read_bytes()
            if b"\0" in data:
                raise ValueError("Binary files cannot be edited as text")
            data.decode("utf-8-sig")
        return path

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        """Replace only after the complete file is flushed; keep the original on failure."""
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if path.exists():
                shutil.copymode(path, temporary)
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def write(self, file_path: str, content: str) -> WriteResult:
        """Write complete UTF-8 text atomically inside the workspace."""
        try:
            path = self._text_path(file_path)
            self._atomic_write(path, content.encode("utf-8"))
            return WriteResult(path=file_path)
        except (OSError, ValueError) as exc:
            return WriteResult(error=f"Write failed: {exc}")

    def edit(
        self, file_path: str, old_string: str, new_string: str, replace_all: bool = False
    ) -> EditResult:
        """Apply exact replacement while preserving BOM and existing newline bytes."""
        try:
            path = self._text_path(file_path)
            original = path.read_bytes()
            text = original.decode("utf-8-sig")
            # Map normalized match positions back to the original, preserving untouched mixed line endings.
            normalized = text.replace("\r\n", "\n").replace("\r", "\n")
            old_string = old_string.replace("\r\n", "\n").replace("\r", "\n")
            new_string = new_string.replace("\r\n", "\n").replace("\r", "\n")
            if not old_string:
                return EditResult(error="old_string must not be empty")
            occurrences = normalized.count(old_string)
            if not occurrences:
                return EditResult(
                    error="String not found; read the current file and use an exact match."
                )
            if occurrences > 1 and not replace_all:
                return EditResult(
                    error=f"String appears {occurrences} times; provide unique surrounding context or replace_all=True."
                )
            positions = [0]
            i = 0
            while i < len(text):
                i += 2 if text[i : i + 2] == "\r\n" else 1
                positions.append(i)
            newline = "\r\n" if "\r\n" in text else "\r" if "\r" in text else "\n"
            replacement = new_string.replace("\n", newline)
            matches = list(re.finditer(re.escape(old_string), normalized))
            for match in reversed(matches):
                text = (
                    text[: positions[match.start()]] + replacement + text[positions[match.end()] :]
                )
            data = (b"\xef\xbb\xbf" if original.startswith(b"\xef\xbb\xbf") else b"") + text.encode(
                "utf-8"
            )
            self._atomic_write(path, data)
            return EditResult(path=file_path, occurrences=occurrences)
        except (OSError, ValueError) as exc:
            return EditResult(error=f"Edit failed: {exc}")


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
    if target != ":memory:":
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
    if target != ":memory:":
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
