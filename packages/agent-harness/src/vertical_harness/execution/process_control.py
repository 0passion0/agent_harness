"""执行器共享的环境过滤与进程树终止，不负责命令、日志或作业管理。"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
from contextlib import suppress
from pathlib import Path


class ProcessControl:
    @staticmethod
    def environment() -> dict[str, str]:
        values = {
            key: value
            for key, value in os.environ.items()
            if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE"}
        }
        values.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        return values

    @staticmethod
    def terminate(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        if sys.platform == "win32":
            killed = subprocess.run(
                [
                    str(Path(os.environ["SYSTEMROOT"]) / "System32" / "taskkill.exe"),
                    "/PID",
                    str(process.pid),
                    "/T",
                    "/F",
                ],
                capture_output=True,
            )
            if killed.returncode != 0 and process.poll() is None:
                raise RuntimeError(killed.stdout.decode(errors="replace"))
        else:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
        process.wait()

    @staticmethod
    async def _terminate_async(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        if sys.platform == "win32":
            killer = await asyncio.create_subprocess_exec(
                str(Path(os.environ["SYSTEMROOT"]) / "System32" / "taskkill.exe"),
                "/PID",
                str(process.pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            errors, _ = await killer.communicate()
            if killer.returncode != 0 and process.returncode is None:
                raise RuntimeError(errors.decode(errors="replace"))
        else:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
        await process.wait()

    @classmethod
    async def aterminate(cls, process: asyncio.subprocess.Process) -> None:
        """重复取消也必须等待进程树退出，再将取消传播给调用者。"""
        cleanup = asyncio.create_task(cls._terminate_async(process))
        cancelled = False
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                cancelled = True
        await cleanup
        if cancelled:
            raise asyncio.CancelledError
