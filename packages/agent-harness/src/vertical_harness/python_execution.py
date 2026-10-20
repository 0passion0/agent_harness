"""Execute saved Python scripts with the harness runtime."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from uuid import uuid4

from langchain_core.tools import tool


def python_file_tool(workspace: Path, interpreter: str):
    @tool
    async def execute_python_file(file_path: str) -> dict:
        """Run an existing workspace .py script with the selected Python interpreter.

        First write complete code with write_file/edit_file, then pass its relative or virtual
        path (analysis.py or /analysis.py). Code strings and shell commands are not accepted.
        Scripts run with the user's permissions, not in a security sandbox; their working
        directory is the workspace. Variables do not persist between runs. Print concise
        results and save deliverables to files. Check exit_code before reporting success.
        Full stdout/stderr are saved to the returned workspace paths; only previews enter
        the conversation. Execution is limited to 120 seconds, with no automatic retry.
        """
        if not file_path.strip():
            raise ValueError("Script path must not be blank")
        script = (workspace / file_path.removeprefix("/")).resolve(strict=True)
        if not script.is_relative_to(workspace.resolve()):
            raise ValueError("Script path must remain inside the workspace")
        if script.suffix != ".py" or not script.is_file():
            raise ValueError("Script path must name a .py file")
        # Do not pass the agent's API credential environment variables to subprocesses.
        env = {key: value for key, value in os.environ.items()
               if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME", "USERPROFILE"}}
        env.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        run_dir = workspace / "runs" / "python" / uuid4().hex
        run_dir.mkdir(parents=True)
        stdout_path, stderr_path = run_dir / "stdout.txt", run_dir / "stderr.txt"
        # Keep complete logs on disk so verbose scripts cannot fill the host's memory.
        with stdout_path.open("wb") as stdout_file, stderr_path.open("wb") as stderr_file:
            process = await asyncio.create_subprocess_exec(
                interpreter, "-X", "utf8", "-E", "-s", str(script), cwd=workspace, env=env,
                stdin=asyncio.subprocess.DEVNULL, stdout=stdout_file, stderr=stderr_file,
            )
            timed_out = False
            try:
                await asyncio.wait_for(process.wait(), timeout=120)
            except TimeoutError:
                timed_out = True
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
        with stdout_path.open(encoding="utf-8") as stdout_text:
            output = stdout_text.read(12001)
        with stderr_path.open(encoding="utf-8") as stderr_text:
            errors = stderr_text.read(12001)
        return {
            "file_path": "/" + script.relative_to(workspace.resolve()).as_posix(),
            "stdout_path": "/" + stdout_path.relative_to(workspace).as_posix(),
            "stderr_path": "/" + stderr_path.relative_to(workspace).as_posix(),
            "exit_code": process.returncode,
            "timed_out": timed_out,
            "error": "Python execution exceeded 120 seconds; no automatic retry was performed." if timed_out else None,
            "stdout": output[:12000], "stderr": errors[:12000],
            "stdout_truncated": len(output) > 12000, "stderr_truncated": len(errors) > 12000,
        }

    return execute_python_file
