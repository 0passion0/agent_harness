"""多线程文件写操作排他互斥锁与协作式取消上下文模块。

本模块提供并发文件原子写入管理工具 `FileMutation`：
1. 粒度锁管理：基于物理路径规范化字符串通过弱引用字典（`WeakValueDictionary`）维护文件锁，
   多路径按字典序排序申请以彻底规避哲学家就餐死锁；
2. 协作式中断：集成 `ContextVar` 机制，在获取锁循环和写入前频繁轮询取消探针，支持外部安全中断；
3. 临时文件原子替换：利用 `NamedTemporaryFile` + `os.fsync` + `os.replace` 实现真正的崩溃一致性原子写入。
"""

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
    """文件写入并发互斥管理器，提供跨线程文件粒度重入锁与可中断取消探针。

    纯静态工具类，用于协调不同线程对工作区文件的并发写入、补丁应用及原子替换。

    Attributes:
        locks (ClassVar[WeakValueDictionary[str, RLock]]): 跨所有调用方共享的文件路径弱引用锁表，
            当无任何线程持有或等待特定文件的锁时由 Python GC 自动回收。
        guard (threading.Lock): 保护锁注册表线程安全并发读写的互斥锁。
        cancelled (ContextVar[threading.Event | None]): 存储当前异步协程或执行线程关联的取消通知事件对象。
    """

    # 跨实例共享的文件路径弱引用锁字典，无线程等待时自动垃圾回收
    locks: ClassVar[WeakValueDictionary[str, RLock]] = WeakValueDictionary()
    # 保护锁字典注册过程的全局线程互斥锁
    guard = threading.Lock()
    # 异步协程向同步写线程传递协同取消信号的 ContextVar 探针
    cancelled: ContextVar[threading.Event | None] = ContextVar("cancel_mutation", default=None)

    @staticmethod
    def check_cancelled() -> None:
        """检查当前协程/线程上下文中的取消事件，若已置位则抛出 CancelledError。

        Raises:
            CancelledError: 当检测到当前上下文绑定的取消事件已被触发置位时抛出。
        """
        event = FileMutation.cancelled.get()
        if event is not None and event.is_set():
            raise CancelledError()

    @staticmethod
    @contextmanager
    def acquire(paths: list[Path]) -> Iterator[None]:
        """按规范路径排序获取多个文件的排他锁，防止死锁并持续检测取消状态。

        将输入的路径列表去重并按字典序升序排序，然后依次循环尝试获取锁（每次超时 50ms）。
        在锁竞争期间定期调用 `check_cancelled()` 确保外部任务取消时能够及时中断等待。
        借助 `ExitStack` 确保上下文退出时（无论正常或异常）所有已持有的锁均被如数释放。

        Args:
            paths (list[Path]): 本次修改涉及的一个或多个目标物理文件路径列表。

        Yields:
            None: 当且仅当所有相关文件的重入锁均已成功持有且未收到取消信号时激活代码块。

        Raises:
            CancelledError: 若在等待锁或获得锁后的断言检查中发现取消信号已置位。
        """
        # 对目标路径去重并按字符串升序排序，严格保证全局加锁次序以杜绝死锁
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
        """将完整字节数据原子性落盘写入目标路径；写入失败时完整保留原文件。

        在目标文件同级目录下创建临时文件，写入数据并执行 `flush()` 与 `os.fsync()` 确保物理刷盘，
        复制原文件权限模式，随后通过 `os.replace` 完成原子替换。若发生异常，临时文件在 `finally` 中被安全清理。

        Args:
            path (Path): 最终落盘的目标文件物理绝对路径。
            data (bytes): 准备全量写入目标文件的原始二进制字节流。

        Raises:
            CancelledError: 若写入前或原子替换前收到协作取消信号。
            OSError: 若文件系统发生 IO 错误或权限不足。
        """
        FileMutation.check_cancelled()
        # 确保目标父级目录物理存在
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            # 在同一文件系统目录下创建临时文件，以保证 os.replace 是原子操作
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(data)
                stream.flush()
                # 强制操作系统将内核缓冲区脏页刷入底层存储介质
                os.fsync(stream.fileno())

            # 尽可能同步源文件的权限模式
            if path.exists():
                shutil.copymode(path, temporary)

            FileMutation.check_cancelled()
            # 原子文件替换（跨平台具备原子语义）
            os.replace(temporary, path)
        finally:
            # 无论成功与否，清理临时文件残留
            if temporary is not None:
                temporary.unlink(missing_ok=True)
