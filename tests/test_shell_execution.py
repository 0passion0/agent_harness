import asyncio
import os
import shlex
import subprocess
import sys

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import ToolException
from pydantic import ValidationError

from vertical_harness.execution.process_control import ProcessControl
from vertical_harness.execution.shell_execution import ShellExecution


def python_command(*args):
    argv = [sys.executable, *args]
    return subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)


def test_shell_sync_runs_in_workdir_without_agent_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-inherited")
    workdir = tmp_path / "nested dir"
    workdir.mkdir()
    (workdir / "probe script.py").write_text(
        "import os; from pathlib import Path; "
        "assert 'OPENAI_API_KEY' not in os.environ; "
        "Path('result.txt').write_text('verified'); print('success')",
        encoding="utf-8",
    )
    tool = ShellExecution.shell_tool(tmp_path)
    result = tool.invoke({"command": python_command("probe script.py"), "workdir": "nested dir"})
    assert result["exit_code"] == 0
    assert not result["timed_out"]
    assert result["workdir"] == str(workdir)
    assert (workdir / "result.txt").read_text() == "verified"
    assert result["output"].strip() == "success"
    assert (tmp_path / result["output_path"].removeprefix("/")).read_text().strip() == "success"


@pytest.mark.parametrize("line", ["x", "x" * 100])
def test_shell_async_returns_failure_and_durable_trailing_output(tmp_path, line):
    (tmp_path / "probe.py").write_text(
        f"import sys; print(({line!r} + '\\n') * 3000, end=''); "
        "print('last error', file=sys.stderr); sys.exit(7)",
        encoding="utf-8",
    )
    result = asyncio.run(
        ShellExecution.shell_tool(tmp_path).ainvoke({"command": python_command("-u", "probe.py")})
    )
    assert result["exit_code"] == 7
    assert result["truncated"]
    assert "last error" in result["output"]
    assert len(result["output"].splitlines()) <= 2002
    output_path = tmp_path / result["output_path"].removeprefix("/")
    assert output_path.read_text().count(line) >= 3000
    assert output_path.stat().st_size > len(result["output"])


def test_shell_validation_precedes_output_creation(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "file.txt").write_text("not a directory")
    tool = ShellExecution.shell_tool(workspace)
    for args, message in [
        ({"command": " "}, "must not be blank"),
        ({"command": "echo test", "workdir": ".."}, "inside the workspace"),
        ({"command": "echo test", "workdir": "file.txt"}, "must exist and be a directory"),
        ({"command": "echo test", "workdir": "missing"}, "must exist and be a directory"),
    ]:
        assert message in tool.invoke(args)
        with pytest.raises(ToolException, match=message):
            asyncio.run(ShellExecution(workspace).aexecute(**args))
    with pytest.raises(ValidationError, match="greater than or equal"):
        tool.invoke({"command": "echo test", "timeout": -1})
    assert not (workspace / "runs").exists()


@pytest.mark.parametrize("link_kind", ["symlink", "junction"])
def test_shell_rejects_output_link_outside_workspace(tmp_path, link_kind):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    if link_kind == "junction":
        if os.name != "nt":
            pytest.skip("Directory junctions are Windows-specific")
        subprocess.run(
            ["cmd.exe", "/c", "mklink", "/J", str(workspace / "runs"), str(outside)],
            check=True,
            capture_output=True,
        )
    else:
        try:
            (workspace / "runs").symlink_to(outside, target_is_directory=True)
        except OSError as error:
            pytest.skip(f"Directory symlinks are unavailable: {error}")
    assert "output directory must remain inside" in ShellExecution.shell_tool(workspace).invoke(
        {"command": "echo must-not-run"}
    )
    assert not list(outside.iterdir())


def test_shell_timeout_retains_output(tmp_path):
    (tmp_path / "slow.py").write_text(
        "import time; print('started', flush=True); time.sleep(60)", encoding="utf-8"
    )
    result = ShellExecution.shell_tool(tmp_path).invoke(
        {"command": python_command("slow.py"), "timeout": 1000}
    )
    assert result["timed_out"]
    assert result["exit_code"] != 0
    assert "started" in result["output"]


def test_shell_repeated_cancellation_stops_descendants(tmp_path, monkeypatch):
    (tmp_path / "child.py").write_text(
        "from pathlib import Path; import time; "
        "Path('ready').touch(); time.sleep(1); Path('escaped').touch()",
        encoding="utf-8",
    )
    (tmp_path / "parent.py").write_text(
        "import subprocess, sys, time; "
        "subprocess.Popen([sys.executable, 'child.py']); time.sleep(60)",
        encoding="utf-8",
    )

    async def run():
        cleanup_started = asyncio.Event()
        cleanup_release = asyncio.Event()
        terminate = ProcessControl._terminate_async

        async def delayed_terminate(process):
            cleanup_started.set()
            await cleanup_release.wait()
            await terminate(process)

        monkeypatch.setattr(ProcessControl, "_terminate_async", staticmethod(delayed_terminate))
        task = asyncio.create_task(
            ShellExecution.shell_tool(tmp_path).ainvoke(
                {"command": python_command("parent.py"), "timeout": 0}
            )
        )
        try:
            async with asyncio.timeout(10):
                while not (tmp_path / "ready").exists():
                    await asyncio.sleep(0.01)
        finally:
            task.cancel()
            try:
                await asyncio.wait_for(cleanup_started.wait(), timeout=10)
                task.cancel()
                await asyncio.sleep(0)
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done()
            finally:
                cleanup_release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        await asyncio.sleep(1.1)
        assert not (tmp_path / "escaped").exists()

    asyncio.run(run())


def test_shell_handles_empty_output_and_absolute_workdir(tmp_path):
    result = ShellExecution.shell_tool(tmp_path).invoke(
        {"command": python_command("-c", "pass"), "workdir": str(tmp_path), "timeout": 0}
    )
    assert result["exit_code"] == 0
    assert result["output"] == "(no output)"
    assert not result["truncated"]


def test_shell_unexpected_spawn_failure_propagates(tmp_path, monkeypatch):
    async def fail_spawn(*args, **kwargs):
        raise OSError("spawn unavailable")

    monkeypatch.setattr(asyncio, "create_subprocess_shell", fail_spawn)
    with pytest.raises(OSError, match="spawn unavailable"):
        ShellExecution.shell_tool(tmp_path).invoke({"command": "echo test"})


def test_shell_graph_recovers_from_invalid_workdir(tmp_path):
    class Model(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    command = python_command("-c", "print('verified')")
    agent = create_agent(
        model=Model(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": "bad-cwd",
                            "name": "shell",
                            "args": {"command": command, "workdir": "missing"},
                        }
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": "corrected-cwd",
                            "name": "shell",
                            "args": {"command": command, "workdir": str(tmp_path)},
                        }
                    ],
                ),
                AIMessage(content="done"),
            ]
        ),
        tools=[ShellExecution.shell_tool(tmp_path)],
    )
    result = agent.invoke({"messages": [HumanMessage(content="Verify the workspace")]})
    messages = [message for message in result["messages"] if isinstance(message, ToolMessage)]
    assert messages[0].status == "error"
    assert "must exist and be a directory" in messages[0].content
    assert messages[1].status == "success"
    assert "verified" in messages[1].content
    assert result["messages"][-1].content == "done"
