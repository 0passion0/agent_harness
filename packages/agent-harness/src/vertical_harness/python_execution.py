"""在智能体工作区中受控执行已持久化 Python 脚本的运行时工具模块。"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from langchain_core.tools import tool


@dataclass(eq=False)
class PythonExecution:
    workspace: Path
    interpreter: str

    async def execute_python_file(self, file_path: str) -> dict:
        """Run an existing workspace .py script with the selected Python interpreter.

        Create a runnable script with write_file; for subsequent changes, read only the affected
        lines and use edit_file for targeted replacements. Pass its relative or virtual path
        (analysis.py or /analysis.py). Code strings and shell commands are not accepted.
        Scripts run with the user's permissions, not in a security sandbox; their working
        directory is the workspace. Variables do not persist between runs. Print concise
        results and save deliverables to files. Check exit_code before reporting success.
        Full stdout/stderr are saved to the returned workspace paths; only previews enter
        the conversation. Execution is limited to 120 seconds, with no automatic retry.

        :param file_path: 工作区内的相对脚本文件路径（如 'analysis.py' 或 '/analysis.py'）。
        :type file_path: str
        :return: 包含退出码、输出路径、文本预览及超时标记的字典载荷。
        :rtype: dict

        .. code-block:: python

            # return 数据结构契约：
            {
                "file_path": "/analysis.py",
                "stdout_path": "/runs/python/a1b2c3d4/stdout.txt",
                "stderr_path": "/runs/python/a1b2c3d4/stderr.txt",
                "exit_code": 0,
                "timed_out": False,
                "error": None,
                "stdout": "执行结果...",
                "stderr": "",
                "stdout_truncated": False,
                "stderr_truncated": False
            }

        :raises ValueError: 当脚本路径为空、超出工作区边界或不是 .py 文件时抛出。
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：校验并解析脚本路径，防止目录穿越攻击
        if not file_path.strip():
            raise ValueError("Script path must not be blank")
        script = (self.workspace / file_path.removeprefix("/")).resolve(strict=True)
        if not script.is_relative_to(self.workspace.resolve()):
            raise ValueError("Script path must remain inside the workspace")
        if script.suffix != ".py" or not script.is_file():
            raise ValueError("Script path must name a .py file")

        # 第二步：构建安全隔离的环境变量（严格剔除可能含智能体 API 密钥的环境变量）
        env = {
            key: value
            for key, value in os.environ.items()
            if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE"}
        }
        env.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1")

        # 第三步：创建本次执行专用的运行日志目录与重定向输出文件
        run_dir = self.workspace / "runs" / "python" / uuid4().hex
        run_dir.mkdir(parents=True)
        stdout_path, stderr_path = run_dir / "stdout.txt", run_dir / "stderr.txt"

        # 第四步：启动异步子进程执行脚本，强制 UTF-8 编码与无依赖运行模式
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
            )
            timed_out = False
            try:
                # 第五步：等待执行完毕，设置 120 秒硬超时限制
                await asyncio.wait_for(process.wait(), timeout=120)
            except TimeoutError:
                timed_out = True
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()

        # 第六步：读取并截取前 12000 个字符的输出预览，防止大输出撑爆上下文
        with stdout_path.open(encoding="utf-8") as stdout_text:
            output = stdout_text.read(12001)
        with stderr_path.open(encoding="utf-8") as stderr_text:
            errors = stderr_text.read(12001)

        # 第七步：组装并返回标准化的执行结果字典
        return {
            "file_path": "/" + script.relative_to(self.workspace.resolve()).as_posix(),
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


    @staticmethod
    def python_file_tool(workspace: Path, interpreter: str) -> Callable[..., Any]:
        """构建绑定了目标工作区根路径与特定 Python 解释器的脚本执行工具。

        :param workspace: 脚本运行的工作区物理根路径。
        :type workspace: pathlib.Path
        :param interpreter: Python 解释器可执行文件物理路径。
        :type interpreter: str
        :return: 经过 LangChain @tool 包装的异步 execute_python_file 绑定方法。
        :rtype: collections.abc.Callable[..., typing.Any]
        :author: tongliga
        :date: 2026-10-21
        """
        return tool(PythonExecution(workspace, interpreter).execute_python_file)


python_file_tool = PythonExecution.python_file_tool
