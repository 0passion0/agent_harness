"""在智能体工作区中受控执行已持久化 Python 脚本的运行时工具模块。"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from langchain_core.tools import BaseTool, tool

from vertical_harness.execution.process_control import ProcessControl


@dataclass(eq=False)
class PythonExecution:
    """受控 Python 脚本执行器，在隔离的环境变量和指定工作区内异步执行 Python 文件。"""

    workspace: Path
    interpreter: str

    async def execute_python_file(self, file_path: str) -> dict[str, Any]:
        """Run an existing workspace .py script with the selected Python interpreter."""
        try:
            if not file_path.strip():
                raise ValueError("Script path must not be blank")
            workspace_root = self.workspace.resolve()
            script = (self.workspace / file_path.removeprefix("/")).resolve()
            if not script.is_relative_to(workspace_root):
                raise ValueError("Script path must remain inside the workspace")
            if script.suffix != ".py":
                raise ValueError("Script path must name a .py file")
            if not script.exists():
                raise FileNotFoundError(
                    f"File does not exist: /{script.relative_to(workspace_root).as_posix()}"
                )
            if not script.is_file():
                raise IsADirectoryError(
                    f"Script path must name a regular file, not a directory: /{script.relative_to(workspace_root).as_posix()}"
                )
            env = ProcessControl.environment()
            run_dir = self.workspace / "runs" / "python" / uuid4().hex
            run_dir.mkdir(parents=True)
            stdout_path, stderr_path = run_dir / "stdout.txt", run_dir / "stderr.txt"
            with stdout_path.open("wb") as stdout_file, stderr_path.open("wb") as stderr_file:
                process = await asyncio.create_subprocess_exec(
                    self.interpreter,
                    "-X",
                    "utf8",
                    "-E",
                    "-s",
                    str(script),
                    cwd=self.workspace,
                    env=env,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    start_new_session=os.name != "nt",
                )
                timed_out = False
                try:
                    await asyncio.wait_for(process.wait(), timeout=120)
                except TimeoutError:
                    timed_out = True
                finally:
                    if process.returncode is None:
                        await ProcessControl.aterminate(process)
            with stdout_path.open(encoding="utf-8") as stdout_text:
                output = stdout_text.read(12001)
            with stderr_path.open(encoding="utf-8") as stderr_text:
                errors = stderr_text.read(12001)
            return {
                "file_path": "/" + script.relative_to(workspace_root).as_posix(),
                "stdout_path": "/" + stdout_path.relative_to(self.workspace).as_posix(),
                "stderr_path": "/" + stderr_path.relative_to(self.workspace).as_posix(),
                "exit_code": process.returncode,
                "timed_out": timed_out,
                "error": "Python execution exceeded 120 seconds; no automatic retry was performed."
                if timed_out
                else None,
                "stdout": output[:12000],
                "stderr": errors[:12000],
                "stdout_truncated": len(output) > 12000,
                "stderr_truncated": len(errors) > 12000,
            }
        except Exception as exc:
            # 全局统一异常捕获：返回结构化错误字典并展示异常信息，避免破坏 Agent 图执行
            return {
                "file_path": file_path,
                "stdout_path": None,
                "stderr_path": None,
                "exit_code": 1,
                "timed_out": False,
                "error": f"{type(exc).__name__}: {exc}",
                "stdout": "",
                "stderr": f"{type(exc).__name__}: {exc}",
                "stdout_truncated": False,
                "stderr_truncated": False,
            }

    @staticmethod
    def python_file_tool(workspace: Path, interpreter: str) -> BaseTool:
        """构建绑定了目标工作区根路径与特定 Python 解释器的脚本执行工具。"""
        return tool(PythonExecution(workspace, interpreter).execute_python_file)
