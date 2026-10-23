"""后台 Shell 的固定内部状态，不向工具调用者暴露进程和锁。"""

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
    owner: tuple[str, str, str]
    process: subprocess.Popen[bytes]
    command: str
    cwd: Path
    root: Path
    path: Path
    future: Future[dict[str, Any]]
    deadline: float | None = None
    cancelled: bool = False
    removed: bool = False
    lock: LockType = field(default_factory=Lock)
    finished: Event = field(default_factory=Event)
