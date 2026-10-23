"""多线程文件写操作排他互斥锁与协作式取消上下文模块。"""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
from _thread import RLock
from collections.abc import Iterator
from concurrent.futures import CancelledError
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import ClassVar
from weakref import WeakValueDictionary


class FileMutation:
    """文件写入并发互斥管理器，提供跨线程文件粒度重入锁与可中断取消探针。"""

    locks: ClassVar[WeakValueDictionary[str, RLock]] = WeakValueDictionary()
    guard = threading.Lock()
    cancelled: ContextVar[threading.Event | None] = ContextVar("cancel_mutation", default=None)

    @staticmethod
    def check_cancelled() -> None:
        """检查当前协程/线程上下文中的取消事件，若已置位则抛出 CancelledError。"""
        event = FileMutation.cancelled.get()
        if event is not None and event.is_set():
            raise CancelledError()

    @staticmethod
    @contextmanager
    def acquire(paths: list[Path]) -> Iterator[None]:
        """按规范路径排序获取多个文件的排他锁，防止死锁并持续检测取消状态。"""
        with FileMutation.guard:
            locks = [
                FileMutation.locks.setdefault(str(path), threading.RLock())
                for path in sorted(set(paths))
            ]
        with ExitStack() as stack:
            for lock in locks:
                while not lock.acquire(timeout=0.05):
                    FileMutation.check_cancelled()
                stack.callback(lock.release)
            FileMutation.check_cancelled()
            yield

    @staticmethod
    def atomic_write(path: Path, data: bytes) -> None:
        """将完整字节数据原子性落盘写入目标路径；写入失败时完整保留原文件。"""
        FileMutation.check_cancelled()
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
            FileMutation.check_cancelled()
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
