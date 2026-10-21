"""多线程文件写操作排他互斥锁与协作式取消上下文模块。"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from concurrent.futures import CancelledError
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, ClassVar
from weakref import WeakValueDictionary


class FileMutation:
    """文件写入并发互斥管理器，提供跨线程文件粒度重入锁与可中断取消探针。

    :author: tongliga
    :date: 2026-10-21
    """

    locks: ClassVar[WeakValueDictionary[str, Any]] = WeakValueDictionary()
    guard = threading.Lock()
    cancelled: ContextVar[threading.Event | None] = ContextVar("cancel_mutation", default=None)

    @staticmethod
    def check_cancelled() -> None:
        """检查当前协程/线程上下文中的取消事件，若已置位则抛出 CancelledError。

        :raises concurrent.futures.CancelledError: 当取消事件已被激活时抛出。
        :author: tongliga
        :date: 2026-10-21
        """
        event = FileMutation.cancelled.get()
        if event is not None and event.is_set():
            raise CancelledError()

    @staticmethod
    @contextmanager
    def acquire(paths: list[Path]) -> Iterator[None]:
        """按规范路径排序获取多个文件的排他锁，防止死锁并持续检测取消状态。

        :param paths: 待获取写锁的文件路径列表。
        :type paths: list[pathlib.Path]
        :return: 上下文管理器生成器。
        :rtype: collections.abc.Iterator[None]
        :raises concurrent.futures.CancelledError: 当等待锁期间操作被取消时抛出。
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：在全局保护锁下为所有路径按序分配或复用可重入锁
        with FileMutation.guard:
            locks = [
                FileMutation.locks.setdefault(str(path), threading.RLock())
                for path in sorted(set(paths))
            ]

        # 第二步：使用 ExitStack 按序获取每一个锁，超时轮询检查取消
        with ExitStack() as stack:
            for lock in locks:
                while not lock.acquire(timeout=0.05):
                    FileMutation.check_cancelled()
                stack.callback(lock.release)
            FileMutation.check_cancelled()
            yield
