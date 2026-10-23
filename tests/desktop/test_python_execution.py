import asyncio
import json
import sys

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from vertical_harness import HarnessSettings
from vertical_harness.core.compose import AgentComposer
from vertical_harness.execution.process_control import ProcessControl
from vertical_harness.execution.python_execution import PythonExecution


@pytest.mark.parametrize("stop", ["cancel", "timeout"])
def test_python_stops_descendants_before_returning(tmp_path, monkeypatch, stop):
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
    wait_for = asyncio.wait_for

    async def bounded_wait(awaitable, timeout):
        if timeout == 120:
            async with asyncio.timeout(10):
                while not (tmp_path / "ready").exists():
                    await asyncio.sleep(0.01)
            timeout = 0.01
        return await wait_for(awaitable, timeout)

    async def run():
        cleanup_started, cleanup_release = asyncio.Event(), asyncio.Event()
        terminate = ProcessControl._terminate_async

        async def delayed_terminate(process):
            cleanup_started.set()
            await cleanup_release.wait()
            await terminate(process)

        if stop == "timeout":
            monkeypatch.setattr(asyncio, "wait_for", bounded_wait)
            cleanup_release.set()
        monkeypatch.setattr(ProcessControl, "_terminate_async", delayed_terminate)
        task = asyncio.create_task(
            PythonExecution(tmp_path, sys.executable).execute_python_file("parent.py")
        )
        try:
            async with asyncio.timeout(10):
                while not (tmp_path / "ready").exists():
                    await asyncio.sleep(0.01)
            if stop == "cancel":
                task.cancel()
                await wait_for(cleanup_started.wait(), 10)
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done()
                cleanup_release.set()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                result = await task
                assert result["timed_out"] and result["exit_code"] != 0
            await asyncio.sleep(1.1)
            assert not (tmp_path / "escaped").exists()
        finally:
            cleanup_release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())


def test_python_uses_workspace_and_configured_interpreter_without_agent_secrets(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-inherited")
    (tmp_path / "helper.py").write_text("MEAN = 2.5", encoding="utf-8")
    code = "import os, sys, subprocess; from pathlib import Path; from helper import MEAN; assert 'OPENAI_API_KEY' not in os.environ; assert subprocess.check_output([sys.executable, '-c', \"print('子进程')\"], text=True).strip() == '子进程'; Path('result.txt').write_text('真实结果', encoding='utf-8'); assert Path(__file__).name == 'analysis.py'; print(sys.executable); print(f'均值={MEAN}')"
    (tmp_path / "analysis.py").write_text(code, encoding="utf-8")
    result = asyncio.run(
        PythonExecution.python_file_tool(tmp_path, sys.executable).ainvoke(
            {"file_path": "/analysis.py"}
        )
    )
    assert result["exit_code"] == 0
    assert sys.executable in result["stdout"]
    assert "均值=2.5" in result["stdout"]
    assert (tmp_path / "result.txt").read_text(encoding="utf-8") == "真实结果"
    assert (tmp_path / result["stdout_path"].removeprefix("/")).read_text(
        encoding="utf-8"
    ) == result["stdout"]


def test_python_exposes_script_failure_and_output_truncation(tmp_path, monkeypatch):
    from vertical_harness.execution import python_execution

    spawn = asyncio.create_subprocess_exec

    async def bounded_spawn(*args, **kwargs):
        assert kwargs["stdout"] != asyncio.subprocess.PIPE
        assert kwargs["stderr"] != asyncio.subprocess.PIPE
        return await spawn(*args, **kwargs)

    monkeypatch.setattr(python_execution.asyncio, "create_subprocess_exec", bounded_spawn)
    tool = PythonExecution.python_file_tool(tmp_path, sys.executable)
    (tmp_path / "analysis.py").write_text(
        "print('中' * 1000000); raise ValueError('bad input')", encoding="utf-8"
    )
    result = asyncio.run(tool.ainvoke({"file_path": "analysis.py"}))
    assert result["exit_code"] != 0
    assert "ValueError: bad input" in result["stderr"]
    assert result["stdout_truncated"]
    assert len(result["stdout"]) == 12000
    assert result["stdout"] == "中" * 12000
    assert (tmp_path / result["stdout_path"].removeprefix("/")).stat().st_size > 12000
    blank_result = asyncio.run(tool.ainvoke({"file_path": " "}))
    assert blank_result["exit_code"] == 1
    assert "must not be blank" in blank_result["error"]


def test_script_path_validation_precedes_execution(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (tmp_path / "outside.py").write_text("raise AssertionError('must not run')", encoding="utf-8")
    (workspace / "data.txt").write_text("not Python", encoding="utf-8")
    tool = PythonExecution.python_file_tool(workspace, sys.executable)
    outside_result = asyncio.run(tool.ainvoke({"file_path": "../outside.py"}))
    assert outside_result["exit_code"] == 1
    assert "inside the workspace" in outside_result["error"]
    not_py_result = asyncio.run(tool.ainvoke({"file_path": "data.txt"}))
    assert not_py_result["exit_code"] == 1
    assert ".py file" in not_py_result["error"]
    missing_result = asyncio.run(tool.ainvoke({"file_path": "missing.py"}))
    assert missing_result["exit_code"] == 1
    assert missing_result["error"] == "FileNotFoundError: File does not exist: /missing.py"
    assert "FileNotFoundError" in missing_result["stderr"]
    assert missing_result["stdout"] == ""
    assert not (workspace / "runs").exists()


def test_cancelling_python_waits_for_process_exit(tmp_path, monkeypatch):
    from vertical_harness.execution import python_execution

    spawn = asyncio.create_subprocess_exec
    processes = []

    async def run():
        started = asyncio.Event()

        async def tracked_spawn(*args, **kwargs):
            process = await spawn(*args, **kwargs)
            processes.append(process)
            started.set()
            return process

        monkeypatch.setattr(python_execution.asyncio, "create_subprocess_exec", tracked_spawn)
        (tmp_path / "analysis.py").write_text("import time; time.sleep(60)", encoding="utf-8")
        task = asyncio.create_task(
            PythonExecution.python_file_tool(tmp_path, sys.executable).ainvoke(
                {"file_path": "analysis.py"}
            )
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert processes[0].returncode is not None

    asyncio.run(run())


def test_python_timeout_returns_failure_with_partial_output(tmp_path, monkeypatch):
    from vertical_harness.execution import python_execution

    wait_for = asyncio.wait_for

    async def short_wait(awaitable, *, timeout):
        assert timeout == 120
        return await wait_for(awaitable, timeout=1)

    monkeypatch.setattr(python_execution.asyncio, "wait_for", short_wait)
    (tmp_path / "analysis.py").write_text(
        "import time; print('已完成第一步', flush=True); time.sleep(60)", encoding="utf-8"
    )
    result = asyncio.run(
        PythonExecution.python_file_tool(tmp_path, sys.executable).ainvoke(
            {"file_path": "analysis.py"}
        )
    )
    assert result["timed_out"] is True
    assert result["exit_code"] != 0
    assert "已完成第一步" in result["stdout"]
    assert "120 seconds" in result["error"]


@pytest.mark.parametrize(
    "mode,approve", [("read_only", False), ("ask", False), ("ask", True), ("full", True)]
)
def test_python_permission_modes_in_composed_graph(tmp_path, monkeypatch, mode, approve):
    from vertical_harness.core import compose

    code = "from pathlib import Path; Path('output.txt').write_text('verified')"
    (tmp_path / "analysis.py").write_text(code, encoding="utf-8")

    class Model(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            assert any(getattr(item, "name", None) == "execute_python_file" for item in tools) == (
                mode != "read_only"
            )
            assert not any(getattr(item, "name", None) == "run_python" for item in tools)
            return self

    monkeypatch.setattr(
        compose.ModelRouting,
        "init_configured_model",
        lambda _settings: Model(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": "python-call",
                            "name": "execute_python_file",
                            "args": {"file_path": "/analysis.py"},
                        }
                    ],
                ),
                AIMessage(content="done"),
            ]
        ),
    )
    settings = HarnessSettings(
        _env_file=None,
        model="test:python",
        workspace_root=tmp_path,
        store_backend="memory",
        enable_memory=False,
    )
    options = {
        "runtimes": {"python": sys.executable},
        "permission_mode": mode,
        "max_context_tokens": 128000,
        "agent_content": "",
    }
    agent = AgentComposer.build_agent(
        HarnessSettings.model_validate({**(settings).model_dump(), **options}),
        checkpointer=InMemorySaver(),
    )

    async def run():
        config = {"configurable": {"thread_id": "python"}}
        inputs = {"messages": [HumanMessage(content="calculate")]}
        if mode == "read_only":
            with pytest.raises(PermissionError, match="disabled in read_only"):
                await agent.ainvoke(inputs, config)
            return
        state = await agent.ainvoke(inputs, config)
        if mode == "ask":
            assert not (tmp_path / "output.txt").exists()
            pending = state["__interrupt__"][0]
            assert pending.value["action_requests"][0]["args"] == {"file_path": "/analysis.py"}
            state = await agent.ainvoke(
                Command(
                    resume={
                        pending.id: {"decisions": [{"type": "approve" if approve else "reject"}]}
                    }
                ),
                config,
            )
        messages = [
            m
            for m in state["messages"]
            if isinstance(m, ToolMessage) and m.name == "execute_python_file"
        ]
        assert len(messages) == 1
        if approve:
            assert json.loads(messages[0].content)["exit_code"] == 0
        else:
            assert messages[0].status == "error"

    asyncio.run(run())
    assert (tmp_path / "output.txt").exists() == (approve and mode != "read_only")
