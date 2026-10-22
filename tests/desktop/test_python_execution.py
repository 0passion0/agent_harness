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
from vertical_harness.tools.python_execution import PythonExecution


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
    from vertical_harness.tools import python_execution

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
    with pytest.raises(ValueError, match="must not be blank"):
        asyncio.run(tool.ainvoke({"file_path": " "}))


def test_script_path_validation_precedes_execution(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (tmp_path / "outside.py").write_text("raise AssertionError('must not run')", encoding="utf-8")
    (workspace / "data.txt").write_text("not Python", encoding="utf-8")
    tool = PythonExecution.python_file_tool(workspace, sys.executable)
    with pytest.raises(ValueError, match="inside the workspace"):
        asyncio.run(tool.ainvoke({"file_path": "../outside.py"}))
    with pytest.raises(ValueError, match="\\.py file"):
        asyncio.run(tool.ainvoke({"file_path": "data.txt"}))
    with pytest.raises(FileNotFoundError):
        asyncio.run(tool.ainvoke({"file_path": "missing.py"}))
    assert not (workspace / "runs").exists()


def test_cancelling_python_waits_for_process_exit(tmp_path, monkeypatch):
    from vertical_harness.tools import python_execution

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
    from vertical_harness.tools import python_execution

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
            assert any(getattr(item, "name", None) == "execute_python_file" for item in tools) == (mode != "read_only")
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
    agent = AgentComposer.build_agent(settings, checkpointer=InMemorySaver(), options=options)

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
