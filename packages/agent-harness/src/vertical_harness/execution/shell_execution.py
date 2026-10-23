"""工作区 Shell 命令受控执行工具模块，支持前台同步运行与后台异步作业生命周期管理。"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
from concurrent.futures import Future
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event, Lock, Thread
from time import monotonic
from typing import Annotated, Any, Literal
from uuid import uuid4

from langchain.tools import ToolRuntime
from langchain_core.tools import StructuredTool, ToolException
from pydantic import Field

from vertical_harness.core.context import RunContext


@dataclass(eq=False)
class ShellExecution:
    """工作区 Shell 命令运行器，管理进程树生命周期、输出文件持久化与多会话作业隔离。

    :author: tongliga
    :date: 2026-10-21
    """

    workspace: Path
    _jobs: dict[str, dict[str, Any]] = field(default_factory=dict, init=False)
    _completed: list[str] = field(default_factory=list, init=False)
    _lock: Any = field(default_factory=Lock, init=False)
    _closed: bool = field(default=False, init=False)

    def execute(
        self,
        command: str,
        workdir: str | None = None,
        timeout: Annotated[int | None, Field(ge=0)] = None,
        background: bool = False,
        runtime: ToolRuntime[RunContext] = None,
    ) -> dict[str, Any]:
        """以同步方式执行 Shell 命令并阻塞等待结果。

        :param command: 待执行的 Shell 命令行。
        :type command: str
        :param workdir: 执行工作目录（相对工作区路径，可选）。
        :type workdir: str | None
        :param timeout: 超时时间（毫秒，默认 120,000ms）。
        :type timeout: int | None
        :param background: 是否作为后台任务启动（默认 False）。
        :type background: bool
        :param runtime: LangChain 工具运行时上下文。
        :type runtime: langchain.tools.ToolRuntime[vertical_harness.core.context.RunContext]
        :return: 命令执行结果字典。
        :rtype: dict[str, typing.Any]
        :author: tongliga
        :date: 2026-10-21
        """
        return asyncio.run(self.aexecute(command, workdir, timeout, background, runtime))

    async def aexecute(
        self,
        command: str,
        workdir: str | None = None,
        timeout: Annotated[int | None, Field(ge=0)] = None,
        background: bool = False,
        runtime: ToolRuntime[RunContext] = None,
    ) -> dict[str, Any]:
        """以异步非阻塞方式执行 Shell 命令，支持前台与后台模式。

        :param command: 待运行的 Shell 命令行字符串。
        :type command: str
        :param workdir: 相对工作区目录。
        :type workdir: str | None
        :param timeout: 超时时间（毫秒）。
        :type timeout: int | None
        :param background: 是否启动为后台任务。
        :type background: bool
        :param runtime: 运行时上下文环境。
        :type runtime: langchain.tools.ToolRuntime[vertical_harness.core.context.RunContext]
        :return: 包含退出码、输出路径与内容截断预览的结果字典。
        :rtype: dict[str, typing.Any]
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：后台任务直接交由后台作业管理器分发
        if background:
            return self._start_background(
                command, workdir, 0 if timeout is None else timeout, runtime
            )

        # 第二步：准备工作区目录、环境变量与输出日志持久化文件
        timeout = 120_000 if timeout is None else timeout
        root, cwd, env, executable, output_path = self._prepare(command, workdir)
        timed_out = False

        # 第三步：创建子进程并重定向输出流至磁盘文件
        with output_path.open("wb") as output_file:
            process = await asyncio.create_subprocess_shell(
                command,
                executable=executable,
                cwd=cwd,
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=output_file,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=os.name != "nt",
            )
            try:
                await asyncio.wait_for(process.wait(), timeout=timeout / 1000 if timeout else None)
            except TimeoutError:
                timed_out = True
            finally:
                if process.returncode is None:
                    cleanup = asyncio.create_task(self._terminate(process))
                    cancelled = False
                    while not cleanup.done():
                        try:
                            await asyncio.shield(cleanup)
                        except asyncio.CancelledError:
                            cancelled = True
                    await cleanup
                    if cancelled:
                        raise asyncio.CancelledError

        # 第四步：构建并返回标准化命令执行载荷
        return self._result(command, cwd, root, output_path, process.returncode, timed_out)

    def _prepare(
        self, command: str, workdir: str | None
    ) -> tuple[Path, Path, dict[str, str], str, Path]:
        """校验参数合法性、工作区目录边界并分配唯一的持久化输出文件。

        :param command: 命令行文本。
        :type command: str
        :param workdir: 工作子目录相对路径。
        :type workdir: str | None
        :return: (根目录, 工作目录, 环境变量, 可执行解释器, 日志输出文件路径) 元组。
        :rtype: tuple[pathlib.Path, pathlib.Path, dict[str, str], str, pathlib.Path]
        :raises ToolException: 命令为空或目录越出工作区边界时抛出。
        :author: tongliga
        :date: 2026-10-21
        """
        if not command.strip():
            raise ToolException("Shell command must not be blank")
        root = self.workspace.resolve(strict=True)
        cwd = (root / workdir).resolve() if workdir is not None else root
        if not cwd.is_relative_to(root):
            raise ToolException("Shell working directory must remain inside the workspace")
        if not cwd.is_dir():
            raise ToolException("Shell working directory must exist and be a directory")

        env = {
            key: value
            for key, value in os.environ.items()
            if key.upper()
            in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE"}
        }
        env.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        if os.name == "nt":
            executable = str(Path(os.environ["SYSTEMROOT"]) / "System32" / "cmd.exe")
        else:
            executable = "/bin/sh"

        run_dir = (root / "runs" / "shell" / uuid4().hex).resolve()
        if not run_dir.is_relative_to(root):
            raise ToolException("Shell output directory must remain inside the workspace")
        run_dir.mkdir(parents=True)
        output_path = run_dir / "output.txt"
        return root, cwd, env, executable, output_path

    def _result(
        self,
        command: str,
        cwd: Path,
        root: Path,
        output_path: Path,
        exit_code: int | None,
        timed_out: bool,
    ) -> dict[str, Any]:
        """格式化命令结果载荷并提取末尾截断预览。

        :param command: 执行命令。
        :type command: str
        :param cwd: 命令工作目录。
        :type cwd: pathlib.Path
        :param root: 工作区根目录。
        :type root: pathlib.Path
        :param output_path: 输出日志本地物理路径。
        :type output_path: pathlib.Path
        :param exit_code: 进程退出码（运行中或超时为 None）。
        :type exit_code: int | None
        :param timed_out: 是否因超时被强制终止。
        :type timed_out: bool
        :return: 标准化结果字典。
        :rtype: dict[str, typing.Any]
        :author: tongliga
        :date: 2026-10-21
        """
        virtual_output = "/" + output_path.relative_to(root).as_posix()
        output, truncated = self._capture(output_path)
        if truncated:
            output += f"\n\n[output truncated; full output saved to {virtual_output}]"
        return {
            "command": command,
            "workdir": str(cwd),
            "output_path": virtual_output,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "output": output or "(no output)",
            "truncated": truncated,
        }

    @staticmethod
    def _owner(runtime: ToolRuntime[RunContext]) -> tuple[str, str, str]:
        """从运行时上下文中提取 (tenant_id, user_id, thread_id) 作业归属三元组。

        :param runtime: LangChain 工具运行时对象。
        :type runtime: langchain.tools.ToolRuntime[vertical_harness.core.context.RunContext]
        :return: (租户ID, 用户ID, 会话线程ID) 归属三元组。
        :rtype: tuple[str, str, str]
        :raises ToolException: 当缺失身份凭据或 thread_id 时抛出。
        :author: tongliga
        :date: 2026-10-21
        """
        if runtime is None or runtime.context is None:
            raise ToolException(
                "Background commands require trusted run context and a session thread_id"
            )
        thread_id = runtime.config.get("configurable", {}).get("thread_id")
        if not thread_id:
            raise ToolException("Background commands require a session thread_id")
        return runtime.context.tenant_id, runtime.context.user_id, thread_id

    def _start_background(
        self,
        command: str,
        workdir: str | None,
        timeout: int,
        runtime: ToolRuntime[RunContext],
    ) -> dict[str, Any]:
        """初始化并拉起后台作业进程，由独立守护线程进行状态与超时监控。

        :param command: 命令行。
        :type command: str
        :param workdir: 工作目录。
        :type workdir: str | None
        :param timeout: 超时时间（毫秒）。
        :type timeout: int
        :param runtime: 工具运行时上下文。
        :type runtime: langchain.tools.ToolRuntime[vertical_harness.core.context.RunContext]
        :return: 包含 shell_id 与状态的启动凭证字典。
        :rtype: dict[str, typing.Any]
        :author: tongliga
        :date: 2026-10-21
        """
        owner = self._owner(runtime)
        with self._lock:
            if self._closed:
                raise ToolException("Shell execution is closed")
            root, cwd, env, executable, output_path = self._prepare(command, workdir)
            shell_id = output_path.parent.name
            with output_path.open("wb") as output_file:
                process = subprocess.Popen(
                    command,
                    shell=True,
                    executable=executable,
                    cwd=cwd,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=output_file,
                    stderr=subprocess.STDOUT,
                    start_new_session=os.name != "nt",
                )
            job = {
                "owner": owner,
                "process": process,
                "command": command,
                "cwd": cwd,
                "root": root,
                "path": output_path,
                "future": Future(),
                "cancelled": False,
                "lock": Lock(),
                "removed": False,
                "finished": Event(),
                "deadline": monotonic() + timeout / 1000 if timeout else None,
            }
            self._jobs[shell_id] = job
            Thread(target=self._monitor, args=(job,), daemon=True).start()
        return {
            "shell_id": shell_id,
            "status": "running",
            "command": command,
            "output_path": "/" + output_path.relative_to(root).as_posix(),
        }

    def _monitor(self, job: dict[str, Any]) -> None:
        """后台轮询监控工作线程函数，处理超时与退出码派发。

        :param job: 后台作业内部元数据字典。
        :type job: dict[str, typing.Any]
        :author: tongliga
        :date: 2026-10-21
        """
        timed_out = False
        failure = None
        try:
            while job["process"].poll() is None:
                with job["lock"]:
                    deadline = job["deadline"]
                    if (
                        deadline is not None
                        and monotonic() >= deadline
                        and job["process"].poll() is None
                    ):
                        timed_out = True
                        self._stop_background(job)
                        break
                try:
                    job["process"].wait(timeout=0.05)
                except subprocess.TimeoutExpired:
                    continue
            with job["lock"]:
                job["deadline"] = None
                result = self._result(
                    job["command"],
                    job["cwd"],
                    job["root"],
                    job["path"],
                    job["process"].returncode,
                    timed_out,
                )
                result.update(
                    shell_id=job["path"].parent.name,
                    status="cancelled" if job["cancelled"] else "completed",
                )
        except BaseException as error:
            failure = error
        finally:
            with self._lock:
                if not job["removed"]:
                    self._completed.append(job["path"].parent.name)
                    if len(self._completed) > 25:
                        del self._jobs[self._completed.pop(0)]
                    if failure is not None:
                        job["future"].set_exception(failure)
                    else:
                        job["future"].set_result(result)
            job["finished"].set()

    @staticmethod
    def _stop_background(job: dict[str, Any]) -> None:
        """安全强制终止后台进程树（Windows 使用 taskkill /T /F，POSIX 使用 killpg）。

        :param job: 包含目标进程的作业描述字典。
        :type job: dict[str, typing.Any]
        :author: tongliga
        :date: 2026-10-21
        """
        process = job["process"]
        if process.poll() is not None:
            return
        if os.name == "nt":
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

    def status(
        self,
        shell_id: str = "",
        runtime: ToolRuntime[RunContext] = None,
        action: Literal[
            "status", "wait", "cancel", "output", "timeout", "list", "remove"
        ] = "status",
        cursor: Annotated[int, Field(ge=0)] = 0,
        limit: Annotated[int, Field(ge=0)] = 65_536,
        timeout: Annotated[int | None, Field(ge=0)] = None,
    ) -> dict[str, Any]:
        """查询、等待、取消或分页读取当前会话所属的后台 Shell 命令作业。

        :param shell_id: 作业唯一标识 ID。
        :type shell_id: str
        :param runtime: 工具运行时上下文。
        :type runtime: langchain.tools.ToolRuntime[vertical_harness.core.context.RunContext]
        :param action: 操作动作 ('status'|'wait'|'cancel'|'output'|'timeout'|'list'|'remove')。
        :type action: typing.Literal["status", "wait", "cancel", "output", "timeout", "list", "remove"]
        :param cursor: 分页读取输出的起始字节偏移量。
        :type cursor: int
        :param limit: 分页读取的最大字节数限制。
        :type limit: int
        :param timeout: 重置的超时时间（毫秒，action='timeout' 时必填）。
        :type timeout: int | None
        :return: 对应的状态载荷或输出字典。
        :rtype: dict[str, typing.Any]
        :raises ToolException: 缺失参数或作业不存在于当前会话时抛出。
        :author: tongliga
        :date: 2026-10-21
        """
        owner = self._owner(runtime)
        if action == "timeout" and timeout is None:
            raise ToolException("The timeout action requires timeout in milliseconds")
        with self._lock:
            if action == "list":
                return {
                    "shells": [
                        {
                            "shell_id": key,
                            "status": "running",
                            "command": value["command"],
                            "workdir": str(value["cwd"]),
                            "output_path": "/"
                            + value["path"].relative_to(value["root"]).as_posix(),
                        }
                        for key, value in self._jobs.items()
                        if value["owner"] == owner
                        and not value["removed"]
                        and value["process"].poll() is None
                    ]
                }
            job = self._jobs.get(shell_id)
            if job is None or job["owner"] != owner or job["removed"]:
                raise ToolException("Background shell not found in this session")

        if action == "remove":
            return self._remove(shell_id, job)
        if action == "output":
            with job["lock"]:
                if job["removed"]:
                    raise ToolException("Background shell not found in this session")
                size = job["path"].stat().st_size
                start = min(cursor, size)
                with job["path"].open("rb") as output_file:
                    output_file.seek(start)
                    data = output_file.read(min(limit, size - start))
                return {
                    "shell_id": shell_id,
                    "output": data.decode("utf-8", errors="replace"),
                    "cursor": start + len(data),
                    "size": size,
                    "truncated": False,
                }
        if action == "timeout":
            with job["lock"]:
                if not job["future"].done() and job["process"].poll() is None:
                    job["deadline"] = monotonic() + timeout / 1000 if timeout else None
        if action == "cancel":
            with job["lock"]:
                if not job["future"].done() and job["process"].poll() is None:
                    job["cancelled"] = True
                    self._stop_background(job)
        if action in {"wait", "cancel"} or job["future"].done():
            return job["future"].result()
        with job["lock"]:
            if job["removed"]:
                raise ToolException("Background shell not found in this session")
            result = self._result(
                job["command"], job["cwd"], job["root"], job["path"], None, False
            )
        return {**result, "shell_id": shell_id, "status": "running"}

    async def astatus(
        self,
        shell_id: str = "",
        runtime: ToolRuntime[RunContext] = None,
        action: Literal[
            "status", "wait", "cancel", "output", "timeout", "list", "remove"
        ] = "status",
        cursor: Annotated[int, Field(ge=0)] = 0,
        limit: Annotated[int, Field(ge=0)] = 65_536,
        timeout: Annotated[int | None, Field(ge=0)] = None,
    ) -> dict[str, Any]:
        """以异步非阻塞形式调用后台作业状态管理。

        :param shell_id: 作业 ID。
        :type shell_id: str
        :param runtime: 工具运行时上下文。
        :type runtime: langchain.tools.ToolRuntime[vertical_harness.core.context.RunContext]
        :param action: 操作动作。
        :type action: typing.Literal["status", "wait", "cancel", "output", "timeout", "list", "remove"]
        :param cursor: 分页读取起始游标。
        :type cursor: int
        :param limit: 分页读取上限。
        :type limit: int
        :param timeout: 超时设置（毫秒）。
        :type timeout: int | None
        :return: 操作执行结果。
        :rtype: dict[str, typing.Any]
        :author: tongliga
        :date: 2026-10-21
        """
        return await asyncio.to_thread(
            self.status, shell_id, runtime, action, cursor, limit, timeout
        )

    def _remove(self, shell_id: str, job: dict[str, Any]) -> dict[str, Any]:
        """停止后台作业并安全清理其在磁盘上的日志文件和注册记录。

        :param shell_id: 作业 ID。
        :type shell_id: str
        :param job: 作业元数据对象。
        :type job: dict[str, typing.Any]
        :return: 移除成功状态字典。
        :rtype: dict[str, typing.Any]
        :author: tongliga
        :date: 2026-10-21
        """
        with job["lock"]:
            if not job["path"].resolve().is_relative_to(job["root"]):
                raise ToolException("Shell output directory must remain inside the workspace")
            with self._lock:
                if job["removed"]:
                    raise ToolException("Background shell not found in this session")
                job["removed"] = True
                if shell_id in self._completed:
                    self._completed.remove(shell_id)
                if not job["future"].done():
                    job["future"].set_exception(
                        ToolException("Background shell not found in this session")
                    )
            self._stop_background(job)
        job["finished"].wait()
        if not job["path"].resolve().is_relative_to(job["root"]):
            raise ToolException("Shell output directory must remain inside the workspace")
        job["path"].unlink()
        with self._lock:
            self._jobs.pop(shell_id, None)
        return {"shell_id": shell_id, "status": "removed"}

    def close(self) -> None:
        """关闭执行器并安全清理所有活跃后台命令作业。

        :author: tongliga
        :date: 2026-10-21
        """
        with self._lock:
            self._closed = True
            jobs = list(self._jobs.values())
        self._close_jobs(jobs)

    def close_session(self, tenant_id: str, user_id: str, thread_id: str) -> None:
        """停止并清理指定会话独占拥有的全部后台命令作业。

        :param tenant_id: 租户 ID。
        :type tenant_id: str
        :param user_id: 用户 ID。
        :type user_id: str
        :param thread_id: 会话线程 ID。
        :type thread_id: str
        :author: tongliga
        :date: 2026-10-21
        """
        owner = tenant_id, user_id, thread_id
        with self._lock:
            jobs = [job for job in self._jobs.values() if job["owner"] == owner]
        self._close_jobs(jobs)

    def _close_jobs(self, jobs: list[dict[str, Any]]) -> None:
        """停止指定列表的后台作业进程并等待其守护线程退出。

        :param jobs: 待终止作业列表。
        :type jobs: list[dict[str, typing.Any]]
        :author: tongliga
        :date: 2026-10-21
        """
        for job in jobs:
            with job["lock"]:
                if job["process"].poll() is None:
                    job["cancelled"] = True
                    self._stop_background(job)
        for job in jobs:
            job["finished"].wait()
            if not job["removed"]:
                job["future"].result()

    async def aclose(self) -> None:
        """以异步非阻塞方式调用 close 方法。

        :author: tongliga
        :date: 2026-10-21
        """
        await asyncio.to_thread(self.close)

    async def aclose_session(self, tenant_id: str, user_id: str, thread_id: str) -> None:
        """以异步非阻塞方式调用 close_session 方法。

        :param tenant_id: 租户 ID。
        :type tenant_id: str
        :param user_id: 用户 ID。
        :type user_id: str
        :param thread_id: 会话线程 ID。
        :type thread_id: str
        :author: tongliga
        :date: 2026-10-21
        """
        await asyncio.to_thread(self.close_session, tenant_id, user_id, thread_id)

    @staticmethod
    async def _terminate(process: asyncio.subprocess.Process) -> None:
        """异步终结子进程及其派生的所有子孙进程。

        :param process: 待终止的异步子进程。
        :type process: asyncio.subprocess.Process
        :author: tongliga
        :date: 2026-10-21
        """
        if os.name == "nt":
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

    @staticmethod
    def _capture(path: Path) -> tuple[str, bool]:
        """限制末尾 50 KiB / 2,000 行的 Shell 输出预览，超出部分在全量输出文件中持久化。

        :param path: 本地输出日志文件路径。
        :type path: pathlib.Path
        :return: (尾部文本预览, 是否被截断) 元组。
        :rtype: tuple[str, bool]
        :author: tongliga
        :date: 2026-10-21
        """
        size = path.stat().st_size
        with path.open("rb") as stream:
            stream.seek(max(0, size - 50 * 1024))
            text = stream.read().decode("utf-8", errors="replace")
        lines = text.split("\n")
        if text.endswith("\n"):
            lines.pop()
        truncated = size > 50 * 1024 or len(lines) > 2000
        return ("\n".join(lines[-2000:]) if len(lines) > 2000 else text), truncated

    @staticmethod
    def shell_tool(workspace: Path) -> StructuredTool:
        """构建单个同步与异步双入口的标准 LangChain 前台命令工具。

        :param workspace: 工作区路径。
        :type workspace: pathlib.Path
        :return: StructuredTool 工具实例。
        :rtype: langchain_core.tools.StructuredTool
        :author: tongliga
        :date: 2026-10-21
        """
        return ShellExecution(workspace).tools()[0]

    def tools(self) -> list[StructuredTool]:
        """构建包含 shell 与 shell_status 的完整执行工具栈，共享后台作业注册表。

        :return: StructuredTool 列表。
        :rtype: list[langchain_core.tools.StructuredTool]
        :author: tongliga
        :date: 2026-10-21
        """
        shell = "cmd.exe" if os.name == "nt" else "/bin/sh"
        command = StructuredTool.from_function(
            name="shell",
            handle_tool_error=True,
            func=self.execute,
            coroutine=self.aexecute,
            description=(
                f"Execute a shell command using {shell}. "
                "Use this for task automation, data processing and repository verification; prefer dedicated "
                "tools for file reads and edits. Quote paths containing spaces. workdir is an existing "
                "relative or absolute directory inside the workspace, defaulting to its root. "
                "timeout is milliseconds (foreground default 120000; background default 0; 0 disables it). Commands run with user "
                "permissions, not in a security sandbox. Environment variables exclude agent secrets. "
                "Full combined stdout/stderr is saved to output_path; output shows the last 50 KiB "
                "and at most 2000 lines. Check exit_code and timed_out before reporting success. "
                "Timeout and cancellation stop the shell and its child processes. "
                "background=true returns a shell_id immediately; use shell_status to read, wait or cancel. "
                "Use background for independent work; wait once when you need its result. Interactive input is unsupported."
            ),
        )
        status = StructuredTool.from_function(
            name="shell_status",
            handle_tool_error=True,
            func=self.status,
            coroutine=self.astatus,
            description=(
                "Read a background command's bounded output and status using shell_id. "
                "action=status returns immediately, wait blocks until completion, cancel stops "
                "the shell and descendants while retaining its result. output reads byte pages "
                "using cursor (default 0) and limit (default 65536), returning cursor and size. "
                "timeout resets its timeout from now in milliseconds (required; 0 clears it). "
                "list returns this session's running shells without shell_id. remove stops a shell "
                "and deletes its output file and status. Only this session's commands are accessible."
            ),
        )
        return [command, status]
