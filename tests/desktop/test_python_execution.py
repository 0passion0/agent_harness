from __future__ import annotations

import asyncio
import json
import sys

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command

from vertical_harness import HarnessSettings, build_agent
from vertical_harness.desktop_python import python_tool
from vertical_harness.desktop_runtime import DesktopPlugin


def test_python_uses_workspace_and_configured_interpreter_without_agent_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-inherited")
    code = (
        "import os, sys, subprocess; from pathlib import Path; "
        "assert 'OPENAI_API_KEY' not in os.environ; "
        "assert subprocess.check_output([sys.executable, '-c', \"print('子进程')\"], text=True).strip() == '子进程'; "
        "Path('result.txt').write_text('真实结果', encoding='utf-8'); "
        "print(sys.executable); print('均值=2.5')"
    )
    result = asyncio.run(python_tool(tmp_path, sys.executable).ainvoke({"code": code}))
    assert result["exit_code"] == 0
    assert sys.executable in result["stdout"]
    assert "均值=2.5" in result["stdout"]
    assert (tmp_path / "result.txt").read_text(encoding="utf-8") == "真实结果"


def test_python_exposes_script_failure_and_output_truncation(tmp_path, monkeypatch):
    from vertical_harness import desktop_python

    spawn = asyncio.create_subprocess_exec

    async def bounded_spawn(*args, **kwargs):
        assert kwargs["stdout"] != asyncio.subprocess.PIPE
        assert kwargs["stderr"] != asyncio.subprocess.PIPE
        return await spawn(*args, **kwargs)

    monkeypatch.setattr(desktop_python.asyncio, "create_subprocess_exec", bounded_spawn)
    tool = python_tool(tmp_path, sys.executable)
    result = asyncio.run(tool.ainvoke({"code": "print('中' * 1000000); raise ValueError('bad input')"}))
    assert result["exit_code"] != 0
    assert "ValueError: bad input" in result["stderr"]
    assert result["stdout_truncated"]
    assert len(result["stdout"]) == 12000
    assert result["stdout"] == "中" * 12000
    with pytest.raises(ValueError, match="must not be blank"):
        asyncio.run(tool.ainvoke({"code": " "}))


def test_cancelling_python_waits_for_process_exit(tmp_path, monkeypatch):
    from vertical_harness import desktop_python

    spawn = asyncio.create_subprocess_exec
    processes = []

    async def run():
        started = asyncio.Event()

        async def tracked_spawn(*args, **kwargs):
            process = await spawn(*args, **kwargs)
            processes.append(process)
            started.set()
            return process

        monkeypatch.setattr(desktop_python.asyncio, "create_subprocess_exec", tracked_spawn)
        task = asyncio.create_task(python_tool(tmp_path, sys.executable).ainvoke({
            "code": "import time; time.sleep(60)",
        }))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert processes[0].returncode is not None

    asyncio.run(run())


def test_python_timeout_returns_failure_with_partial_output(tmp_path, monkeypatch):
    from vertical_harness import desktop_python

    wait_for = asyncio.wait_for

    async def short_wait(awaitable, *, timeout):
        assert timeout == 120
        return await wait_for(awaitable, timeout=1)

    monkeypatch.setattr(desktop_python.asyncio, "wait_for", short_wait)
    result = asyncio.run(python_tool(tmp_path, sys.executable).ainvoke({
        "code": "import time; print('已完成第一步', flush=True); time.sleep(60)",
    }))
    assert result["timed_out"] is True
    assert result["exit_code"] != 0
    assert "已完成第一步" in result["stdout"]
    assert "120 seconds" in result["error"]


@pytest.mark.parametrize("mode,approve", [("read_only", False), ("ask", False), ("ask", True), ("full", True)])
def test_python_permission_modes_in_composed_graph(tmp_path, monkeypatch, mode, approve):
    from vertical_harness.engine import compose

    code = "from pathlib import Path; Path('output.txt').write_text('verified')"

    class Model(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            assert any(getattr(item, "name", None) == "run_python" for item in tools)
            return self

    monkeypatch.setattr(compose, "init_configured_model", lambda _settings: Model(responses=[
        AIMessage(content="", tool_calls=[{"id": "python-call", "name": "run_python", "args": {"code": code}}]),
        AIMessage(content="done"),
    ]))
    settings = HarnessSettings(_env_file=None, model="test:python", workspace_root=tmp_path,
                               store_backend="memory", enable_memory=False)
    options = {"runtimes": {"python": sys.executable}, "permission_mode": mode,
               "max_context_tokens": 4096, "agent_content": "", "skills": []}
    agent = build_agent(settings, store=InMemoryStore(), checkpointer=InMemorySaver(),
                        mcp_tools=[], plugins=[DesktopPlugin(options)])

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
            assert pending.value["action_requests"][0]["args"] == {"code": code}
            state = await agent.ainvoke(Command(resume={pending.id: {
                "decisions": [{"type": "approve" if approve else "reject"}],
            }}), config)
        messages = [m for m in state["messages"] if isinstance(m, ToolMessage) and m.name == "run_python"]
        assert len(messages) == 1
        if approve:
            assert json.loads(messages[0].content)["exit_code"] == 0
        else:
            assert messages[0].status == "error"

    asyncio.run(run())
    assert (tmp_path / "output.txt").exists() == (approve and mode != "read_only")
