"""执行器共享的环境过滤与进程树终止，不负责命令、日志或作业管理。

本模块提供低级进程控制工具 `ProcessControl`：
1. 环境变量过滤隔离：仅放行系统核心白名单变量，防止宿主敏感 API 密钥与环境变量泄露至子进程；
2. 跨平台进程树强制终止：Windows 下利用 `taskkill /T /F`，POSIX 下利用 `killpg(SIGKILL)`，
   防止多进程应用或后台子进程悬挂成为孤儿进程；
3. 异步取消屏蔽保障：在协程取消时确保底层进程树已彻底退出再向上抛出 `CancelledError`。
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
from contextlib import suppress
from pathlib import Path


class ProcessControl:
    """进程控制实用工具类，提供安全环境变量白名单过滤与跨平台进程树硬终止能力。

    纯静态/类方法集合，供 Python 执行器与 Shell 执行器统一复用。
    """

    @staticmethod
    def environment() -> dict[str, str]:
        """构造安全的子进程环境变量字典，仅保留系统核心白名单变量并强制启用 UTF-8 编码。

        过滤掉所有包含宿主 API 密钥、数据库连接字符串等机密信息的环境配置。
        注入 `PYTHONIOENCODING="utf-8"` 与 `PYTHONUTF8="1"`，保证 Python 脚本或 CLI 工具跨平台输入输出均采用 UTF-8 编码。

        Returns:
            dict[str, str]: 过滤且规范化后的环境变量键值对映射字典，包含 PATH、SYSTEMROOT 等系统运行基底。
        """
        # 环境变量白名单过滤：排除包含 API 密钥、数据库凭据等宿主敏感信息的环境变量
        values = {
            key: value
            for key, value in os.environ.items()
            if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE"}
        }
        # 强制标准输出使用 UTF-8 编码，防止 Windows 控制台默认 GBK 编码引发编解码异常
        values.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        return values

    @staticmethod
    def terminate(process: subprocess.Popen[bytes]) -> None:
        """同步阻塞强制杀死指定进程及其整棵派生子进程树，并等待其退出。

        在 Windows 系统下调用 `taskkill.exe /PID <pid> /T /F`；在 POSIX 系统下向该进程的进程组发送 `SIGKILL` 信号。

        Args:
            process (subprocess.Popen[bytes]): 待终止的标准子进程实例。

        Raises:
            RuntimeError: 在 Windows 平台下若 `taskkill` 执行失败且目标进程仍在存活状态时抛出。
        """
        # 若进程已自然退出，则无需执行强杀
        if process.poll() is not None:
            return

        # Windows: taskkill /T /F 终止包含子进程在内的完整进程树
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
            # POSIX: 向进程组发送 SIGKILL 信号终止全部派生进程
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)

        # 阻塞等待进程彻底清理完毕
        process.wait()

    @staticmethod
    async def _terminate_async(process: asyncio.subprocess.Process) -> None:
        """异步非阻塞强制杀死指定异步子进程及其派生进程树，并等待退出完成。

        Args:
            process (asyncio.subprocess.Process): 待终止的 asyncio 子进程对象。

        Raises:
            RuntimeError: 在 Windows 平台下若 `taskkill` 执行失败且目标子进程仍在存活时抛出。
        """
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
        """安全异步终止子进程树，即使当前协程被取消也会等待进程彻底退出后再传播取消异常。

        使用 `asyncio.shield` 保护清理任务不被外层超时或取消中断，防止僵尸进程遗留。

        Args:
            process (asyncio.subprocess.Process): 待安全销毁的异步子进程对象。

        Raises:
            asyncio.CancelledError: 若在等待清理过程中接收到协程取消信号，在进程终止落定后重新抛出。
        """
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
