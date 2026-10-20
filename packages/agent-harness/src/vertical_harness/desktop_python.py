"""Local Python execution exposed through the desktop permission middleware."""

from __future__ import annotations

import asyncio
import os
from io import TextIOWrapper
from pathlib import Path
from tempfile import TemporaryFile

from langchain_core.tools import tool


def python_tool(workspace: Path, interpreter: str):
    @tool
    async def run_python(code: str) -> dict:
        """Execute Python for calculations and creating deliverables using installed libraries.

        Code runs with the current user's permissions, NOT in a security sandbox. Relative paths
        use the workspace directory (use 'source.xlsx', not virtual '/source.xlsx'); variables
        do not persist between calls. Save reusable code
        and results to workspace files. Print concise numeric results and output paths, not rows
        of raw data. The configured interpreter is used; shell commands are not accepted.
        Execution is limited to 120 seconds; a timeout returns failure with partial output.
        """
        if not code.strip():
            raise ValueError("Python code must not be blank")
        # Do not pass the agent's API credential environment variables to subprocesses.
        env = {key: value for key, value in os.environ.items()
               if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE"}}
        env.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        # Spool output to disk so verbose scripts cannot fill the host's memory.
        with TemporaryFile() as stdout_file, TemporaryFile() as stderr_file:
            process = await asyncio.create_subprocess_exec(
                interpreter, "-X", "utf8", "-I", "-", cwd=workspace, env=env,
                stdin=asyncio.subprocess.PIPE, stdout=stdout_file, stderr=stderr_file,
            )
            timed_out = False
            try:
                await asyncio.wait_for(process.communicate(code.encode()), timeout=120)
            except TimeoutError:
                timed_out = True
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
            stdout_file.seek(0)
            stderr_file.seek(0)
            with TextIOWrapper(stdout_file, encoding="utf-8") as stdout_text:
                output = stdout_text.read(12001)
            with TextIOWrapper(stderr_file, encoding="utf-8") as stderr_text:
                errors = stderr_text.read(12001)
        return {
            "exit_code": process.returncode,
            "timed_out": timed_out,
            "error": "Python execution exceeded 120 seconds; no automatic retry was performed." if timed_out else None,
            "stdout": output[:12000], "stderr": errors[:12000],
            "stdout_truncated": len(output) > 12000, "stderr_truncated": len(errors) > 12000,
        }

    return run_python
