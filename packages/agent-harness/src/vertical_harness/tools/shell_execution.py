"""Workspace shell execution for noninteractive task commands."""

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
    """Run session-owned commands with durable output and process cleanup."""

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
        """Execute a shell command; see the shell tool description."""
        return asyncio.run(self.aexecute(command, workdir, timeout, background, runtime))

    async def aexecute(
        self,
        command: str,
        workdir: str | None = None,
        timeout: Annotated[int | None, Field(ge=0)] = None,
        background: bool = False,
        runtime: ToolRuntime[RunContext] = None,
    ) -> dict[str, Any]:
        """Execute a shell command with a workspace working directory."""
        if background:
            return self._start_background(command, workdir, 0 if timeout is None else timeout, runtime)
        timeout = 120_000 if timeout is None else timeout
        root, cwd, env, executable, output_path = self._prepare(command, workdir)
        timed_out = False
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

        return self._result(command, cwd, root, output_path, process.returncode, timed_out)

    def _prepare(self, command: str, workdir: str | None) -> tuple[Path, Path, dict[str, str], str, Path]:
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
            if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE"}
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

    def _result(self, command: str, cwd: Path, root: Path, output_path: Path,
                exit_code: int | None, timed_out: bool) -> dict[str, Any]:
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
        if runtime is None or runtime.context is None:
            raise ToolException("Background commands require trusted run context and a session thread_id")
        thread_id = runtime.config.get("configurable", {}).get("thread_id")
        if not thread_id:
            raise ToolException("Background commands require a session thread_id")
        return runtime.context.tenant_id, runtime.context.user_id, thread_id

    def _start_background(self, command: str, workdir: str | None, timeout: int,
                          runtime: ToolRuntime[RunContext]) -> dict[str, Any]:
        owner = self._owner(runtime)
        with self._lock:
            if self._closed:
                raise ToolException("Shell execution is closed")
            root, cwd, env, executable, output_path = self._prepare(command, workdir)
            shell_id = output_path.parent.name
            with output_path.open("wb") as output_file:
                process = subprocess.Popen(
                    command, shell=True, executable=executable, cwd=cwd, env=env,
                    stdin=subprocess.DEVNULL, stdout=output_file, stderr=subprocess.STDOUT,
                    start_new_session=os.name != "nt",
                )
            job = {
                "owner": owner, "process": process, "command": command, "cwd": cwd,
                "root": root, "path": output_path, "future": Future(),
                "cancelled": False, "lock": Lock(), "removed": False, "finished": Event(),
                "deadline": monotonic() + timeout / 1000 if timeout else None,
            }
            self._jobs[shell_id] = job
            Thread(target=self._monitor, args=(job,), daemon=True).start()
        return {
            "shell_id": shell_id, "status": "running", "command": command,
            "output_path": "/" + output_path.relative_to(root).as_posix(),
        }

    def _monitor(self, job: dict[str, Any]) -> None:
        timed_out = False
        failure = None
        try:
            while job["process"].poll() is None:
                with job["lock"]:
                    deadline = job["deadline"]
                    if deadline is not None and monotonic() >= deadline and job["process"].poll() is None:
                        timed_out = True
                        self._stop_background(job)
                        break
                try:
                    job["process"].wait(timeout=0.05)
                except subprocess.TimeoutExpired:
                    continue
            with job["lock"]:
                job["deadline"] = None
                result = self._result(job["command"], job["cwd"], job["root"], job["path"],
                                      job["process"].returncode, timed_out)
                result.update(shell_id=job["path"].parent.name,
                              status="cancelled" if job["cancelled"] else "completed")
        except BaseException as error:
            failure = error
        finally:
            with self._lock:
                if not job["removed"]:
                    self._completed.append(job["path"].parent.name)
                    if len(self._completed) > 25:
                        del self._jobs[self._completed.pop(0)]
                    if failure is not None:
                        # Transfer worker failures to the caller of status/wait/close.
                        job["future"].set_exception(failure)
                    else:
                        job["future"].set_result(result)
            job["finished"].set()

    @staticmethod
    def _stop_background(job: dict[str, Any]) -> None:
        process = job["process"]
        if process.poll() is not None:
            return
        if os.name == "nt":
            killed = subprocess.run(
                [str(Path(os.environ["SYSTEMROOT"]) / "System32" / "taskkill.exe"),
                 "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
            )
            if killed.returncode != 0 and process.poll() is None:
                raise RuntimeError(killed.stdout.decode(errors="replace"))
        else:
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
        process.wait()

    def status(self, shell_id: str = "", runtime: ToolRuntime[RunContext] = None,
               action: Literal["status", "wait", "cancel", "output", "timeout", "list", "remove"] = "status",
               cursor: Annotated[int, Field(ge=0)] = 0,
               limit: Annotated[int, Field(ge=0)] = 65_536,
               timeout: Annotated[int | None, Field(ge=0)] = None) -> dict[str, Any]:
        """Read output, wait for completion or stop a command owned by this session."""
        owner = self._owner(runtime)
        if action == "timeout" and timeout is None:
            raise ToolException("The timeout action requires timeout in milliseconds")
        with self._lock:
            if action == "list":
                return {"shells": [
                    {"shell_id": key, "status": "running", "command": value["command"],
                     "workdir": str(value["cwd"]),
                     "output_path": "/" + value["path"].relative_to(value["root"]).as_posix()}
                    for key, value in self._jobs.items()
                    if value["owner"] == owner and not value["removed"]
                    and value["process"].poll() is None
                ]}
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
                return {"shell_id": shell_id, "output": data.decode("utf-8", errors="replace"),
                        "cursor": start + len(data), "size": size, "truncated": False}
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
            result = self._result(job["command"], job["cwd"], job["root"], job["path"], None, False)
        return {**result, "shell_id": shell_id, "status": "running"}

    async def astatus(self, shell_id: str = "", runtime: ToolRuntime[RunContext] = None,
                      action: Literal["status", "wait", "cancel", "output", "timeout", "list", "remove"] = "status",
                      cursor: Annotated[int, Field(ge=0)] = 0,
                      limit: Annotated[int, Field(ge=0)] = 65_536,
                      timeout: Annotated[int | None, Field(ge=0)] = None) -> dict[str, Any]:
        """Keep blocking process waits outside the graph event loop."""
        return await asyncio.to_thread(self.status, shell_id, runtime, action, cursor, limit, timeout)

    def _remove(self, shell_id: str, job: dict[str, Any]) -> dict[str, Any]:
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
                    job["future"].set_exception(ToolException("Background shell not found in this session"))
            self._stop_background(job)
        job["finished"].wait()
        if not job["path"].resolve().is_relative_to(job["root"]):
            raise ToolException("Shell output directory must remain inside the workspace")
        job["path"].unlink()
        with self._lock:
            self._jobs.pop(shell_id, None)
        return {"shell_id": shell_id, "status": "removed"}

    def close(self) -> None:
        """Stop all owned commands before the harness workspace is released."""
        with self._lock:
            self._closed = True
            jobs = list(self._jobs.values())
        self._close_jobs(jobs)

    def close_session(self, tenant_id: str, user_id: str, thread_id: str) -> None:
        """Stop one deleted session's commands without closing the shared service."""
        owner = tenant_id, user_id, thread_id
        with self._lock:
            jobs = [job for job in self._jobs.values() if job["owner"] == owner]
        self._close_jobs(jobs)

    def _close_jobs(self, jobs: list[dict[str, Any]]) -> None:
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
        await asyncio.to_thread(self.close)

    async def aclose_session(self, tenant_id: str, user_id: str, thread_id: str) -> None:
        await asyncio.to_thread(self.close_session, tenant_id, user_id, thread_id)

    @staticmethod
    async def _terminate(process: asyncio.subprocess.Process) -> None:
        """Stop the shell and its descendants before releasing output handles."""
        if os.name == "nt":
            killer = await asyncio.create_subprocess_exec(
                str(Path(os.environ["SYSTEMROOT"]) / "System32" / "taskkill.exe"),
                "/PID", str(process.pid), "/T", "/F",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            errors, _ = await killer.communicate()
            if killer.returncode != 0 and process.returncode is None:
                raise RuntimeError(errors.decode(errors="replace"))
        else:
            # A shell can exit between the returncode check and process-group kill.
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
        await process.wait()

    @staticmethod
    def _capture(path: Path) -> tuple[str, bool]:
        """Match OpenCode's trailing 50 KiB / 2,000-line shell preview limits."""
        size = path.stat().st_size
        with path.open("rb") as stream:
            stream.seek(max(0, size - 50 * 1024))
            # Shell output may contain native bytes or start midway through a UTF-8 character.
            text = stream.read().decode("utf-8", errors="replace")
        lines = text.split("\n")
        if text.endswith("\n"):
            lines.pop()
        truncated = size > 50 * 1024 or len(lines) > 2000
        return ("\n".join(lines[-2000:]) if len(lines) > 2000 else text), truncated

    @staticmethod
    def shell_tool(workspace: Path) -> StructuredTool:
        """Build the native LangChain tool with synchronous and asynchronous entry points."""
        return ShellExecution(workspace).tools()[0]

    def tools(self) -> list[StructuredTool]:
        """Build both tools with one process registry; the host must call close/aclose."""
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
            name="shell_status", handle_tool_error=True, func=self.status, coroutine=self.astatus,
            description=("Read a background command's bounded output and status using shell_id. "
                         "action=status returns immediately, wait blocks until completion, cancel stops "
                         "the shell and descendants while retaining its result. output reads byte pages "
                         "using cursor (default 0) and limit (default 65536), returning cursor and size. "
                         "timeout resets its timeout from now in milliseconds (required; 0 clears it). "
                         "list returns this session's running shells without shell_id. remove stops a shell "
                         "and deletes its output file and status. Only this session's commands are accessible."),
        )
        return [command, status]
