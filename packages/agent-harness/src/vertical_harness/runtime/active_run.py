"""单个活跃运行的身份与任务句柄。"""

import asyncio
from dataclasses import dataclass


@dataclass(frozen=True)
class ActiveRun:
    thread_id: str
    run_id: str
    task: asyncio.Task[None]
