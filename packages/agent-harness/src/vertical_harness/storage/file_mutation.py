"""Shared file locks and cooperative cancellation for threaded mutations."""

import threading
from collections.abc import Iterator
from concurrent.futures import CancelledError
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, ClassVar
from weakref import WeakValueDictionary


class FileMutation:
    locks: ClassVar[WeakValueDictionary[str, Any]] = WeakValueDictionary()
    guard = threading.Lock()
    cancelled: ContextVar[threading.Event | None] = ContextVar("cancel_mutation", default=None)

    @staticmethod
    def check_cancelled() -> None:
        event = FileMutation.cancelled.get()
        if event is not None and event.is_set():
            raise CancelledError()

    @staticmethod
    @contextmanager
    def acquire(paths: list[Path]) -> Iterator[None]:
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
