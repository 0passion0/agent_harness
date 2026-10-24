"""后台 Shell 的固定内部状态，不向工具调用者暴露进程和锁。

本模块定义了用于管理后台长期运行 Shell 命令的核心状态类 `ShellJob`。
该类封装了操作系统子进程句柄、结果通知 Future、落盘输出日志路径以及多租户归属三元组，
提供线程安全的锁机制与完成事件通知。
"""

from __future__ import annotations

import subprocess
from _thread import LockType
from concurrent.futures import Future
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event, Lock
from typing import Any


@dataclass
class ShellJob:
    """后台异步 Shell 作业运行实体，封装底层进程句柄、结果 Future 与会话归属。

    为每一个由 `execute` 在后台启动的进程保持状态追踪，支持状态查询、取消、
    日志滚动落盘以及进程树清理。

    Attributes:
        owner (tuple[str, str, str]): 作业归属三元组 `(tenant_id, user_id, session_id)`，
            用于多租户与会话隔离，防止跨用户越权访问或终止他人作业。
        process (subprocess.Popen[bytes]): 底层操作系统的同步子进程实例。
        command (str): 启动该作业时传入的原始 Shell 命令行字符串。
        cwd (Path): 进程执行的实际工作目录，严格约束在工作区安全边界之内。
        root (Path): 所在工作区的绝对物理根路径。
        path (Path): 进程标准输出和标准错误合并重定向写入的本地日志文件绝对物理路径。
        future (Future[dict[str, Any]]): 异步完成通知 Future，解析为包含执行状态和输出摘录的字典。
        deadline (float | None): 执行超时截止单调时间戳（秒），None 表示不设硬超时限制。
        cancelled (bool): 标识该作业是否已被调用方显式主动取消。
        removed (bool): 标识该作业是否已被调用方或垃圾回收器从注册表中解注册并清理。
        lock (LockType): 保护作业生命周期状态（cancelled, removed）并发读写的线程互斥锁。
        finished (Event): 标记监控线程已彻底收尾、子进程退出落盘完毕的线程同步事件。
    """

    # 作业归属三元组 (tenant_id, user_id, session_id)，保障会话与租户隔离
    owner: tuple[str, str, str]
    # 底层同步子进程对象
    process: subprocess.Popen[bytes]
    # 执行的原始 Shell 命令字符串
    command: str
    # 子进程执行工作目录（必须位于工作区边界内）
    cwd: Path
    # 工作区绝对物理根路径
    root: Path
    # 输出与错误重定向落盘文件的绝对物理路径
    path: Path
    # 异步结果 Future，用于向等待方派发执行结果或异常
    future: Future[dict[str, Any]]
    # 超时截止单调时间戳（秒），None 表示无超时
    deadline: float | None = None
    # 作业是否已被主动取消
    cancelled: bool = False
    # 作业是否已从注册表显式移除
    removed: bool = False
    # 保护作业可变状态并发读写的线程互斥锁
    lock: LockType = field(default_factory=Lock)
    # 监控线程与进程树完全清理退出后的就绪事件
    finished: Event = field(default_factory=Event)
