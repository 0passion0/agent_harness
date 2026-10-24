"""在智能体工作区中受控执行已持久化 Python 脚本的运行时工具模块。

本模块提供 `PythonExecution` 执行器及其对应的 LangChain 工具接口。
仅允许执行已安全持久化在工作区内的 `.py` 文件，自动分配独立的运行输出目录并重定向
标准输出与标准错误至日志文件。通过环境变量白名单与隔离参数（`-E -s -X utf8`）进行受控防护，
支持 120 秒超时拦截与进程树强杀。
"""

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
    """受控 Python 脚本执行器，在隔离的环境变量和指定工作区内异步执行 Python 文件。

    Attributes:
        workspace (Path): 智能体操作的根工作区目录，执行的所有脚本和输出文件均受限于该边界内。
        interpreter (str): 用于执行脚本的目标 Python 解释器绝对路径。
    """

    workspace: Path
    interpreter: str

    async def execute_python_file(self, file_path: str) -> dict[str, Any]:
        """Run an existing workspace .py script with the selected Python interpreter.

        在隔离子进程中异步执行指定的工作区 Python 脚本文件。自动捕获并落盘 stdout 与 stderr，
        硬超时时间为 120 秒。所有异常均转化为标准结构化字典返回，避免破坏智能体图调度流程。

        Args:
            file_path (str): 目标 Python 脚本在工作区内的相对路径（例如 `analysis/calculate.py` 或 `/run.py`）。
                必须以 `.py` 为扩展名且必须存在于工作区物理沙箱之内。

        Returns:
            dict[str, Any]: 结构化执行结果字典，包含如下键值：
                - `file_path` (str): 规范化后的工作区相对路径（含前导斜杠 `/`）。
                - `stdout_path` (str | None): 标准输出日志文件相对工作区的路径；若初始化失败则为 None。
                - `stderr_path` (str | None): 标准错误日志文件相对工作区的路径；若初始化失败则为 None。
                - `exit_code` (int | None): 进程退出码，0 表示正常退出，非 0 或异常时为 1。
                - `timed_out` (bool): 标识执行是否超过 120 秒硬超时限制。
                - `error` (str | None): 超时警告或捕获到的底层异常格式化文本；成功时为 None。
                - `stdout` (str): 捕获到的标准输出文本（前 12,000 个字符预览）。
                - `stderr` (str): 捕获到的标准错误文本（前 12,000 个字符预览）或异常描述。
                - `stdout_truncated` (bool): 标准输出是否因超出 12,000 字符限制而截断。
                - `stderr_truncated` (bool): 标准错误是否因超出 12,000 字符限制而截断。
        """
        try:
            if not file_path.strip():
                raise ValueError("Script path must not be blank")
            workspace_root = self.workspace.resolve()
            script = (self.workspace / file_path.removeprefix("/")).resolve()

            # 校验脚本路径安全性：必须位于工作区内、后缀为 .py 且为存在的文件
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

            # 准备环境变量与独立的执行输出目录
            env = ProcessControl.environment()
            run_dir = self.workspace / "runs" / "python" / uuid4().hex
            run_dir.mkdir(parents=True)
            stdout_path, stderr_path = run_dir / "stdout.txt", run_dir / "stderr.txt"

            # 启动受限隔离的 Python 子进程：-X utf8 强制 UTF-8，-E 忽略环境变量，-s 排除用户 site-packages
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
                    # 硬超时限制 120 秒
                    await asyncio.wait_for(process.wait(), timeout=120)
                except TimeoutError:
                    timed_out = True
                finally:
                    # 超时或外部取消时强行杀死进程树
                    if process.returncode is None:
                        await ProcessControl.aterminate(process)

            # 读取前 12,000 字符作为预览返回，超出部分在文件中完整保留
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
        """构建绑定了目标工作区根路径与特定 Python 解释器的脚本执行工具。

        Args:
            workspace (Path): 智能体沙箱物理工作区根目录。
            interpreter (str): 受控 Python 解释器执行路径。

        Returns:
            BaseTool: 封装完成的 LangChain 工具实例，可直接绑定至 Agent 图。
        """
        return tool(PythonExecution(workspace, interpreter).execute_python_file)
