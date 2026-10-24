"""单个活跃运行的身份与任务句柄数据载体模块。

本模块定义了 `ActiveRun` 不可变数据类，用于在 `RunManager` 活跃运行注册表中
安全追踪正在执行的异步任务及其会话和运行身份元数据。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass(frozen=True)
class ActiveRun:
    """当前活跃运行的不可变记录实体，绑定会话身份、轮次标识与异步任务句柄。

    Attributes:
        thread_id (str): 所属会话线程唯一标识符（Thread ID），用于会话级并发排队与状态隔离。
        run_id (str): 当前轮次智能体任务运行的唯一标识符（Run ID），用于关联事件流与追踪日志。
        task (asyncio.Task[None]): 承载本次运行事件流派发执行的底层异步协程任务对象，支持主动取消。
    """

    # 所属会话线程唯一标识符
    thread_id: str
    # 当前轮次运行唯一标识符
    run_id: str
    # 承载本次运行事件流派发的底层异步协程任务
    task: asyncio.Task[None]
