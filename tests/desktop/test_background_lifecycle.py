import asyncio
import json
import os
import shlex
import subprocess
import sys
from io import StringIO
from uuid import uuid4

import pytest
from langchain.tools import ToolRuntime

from vertical_harness import desktop as bridge


def request(tmp_path):
    return {
        "tenant_id": "tenant", "user_id": "user",
        "workspace_root": str(tmp_path / "workspace"),
        "runtime_data_dir": str(tmp_path / "runtime"),
        "options": {
            "model_code": "", "runtimes": {"python": "", "node": ""},
            "permission_mode": "full", "max_context_tokens": 128000, "agent_content": "",
        },
    }


def runtime(loop, thread_id):
    return ToolRuntime(
        state={}, context=loop.identity, config={"configurable": {"thread_id": thread_id}},
        stream_writer=lambda event: None, tool_call_id="background", store=None,
    )


async def start(loop, thread_id):
    root = loop.settings.resolved_workspace_root
    root.mkdir(parents=True, exist_ok=True)
    (root / "slow.py").write_text("import time; time.sleep(60)")
    args = [sys.executable, "-B", "slow.py"]
    command = subprocess.list2cmdline(args) if os.name == "nt" else shlex.join(args)
    return await loop.shell_execution.aexecute(command, background=True, runtime=runtime(loop, thread_id))


def test_delete_session_reconfigure_and_shutdown_own_background_lifetime(tmp_path, monkeypatch):
    monkeypatch.setenv("VA_MODEL", "test:fake")
    loop = bridge.AgentLoop()

    async def run():
        await loop.dispatch("configure", request(tmp_path))
        original = loop.shell_execution
        first_thread, second_thread = str(uuid4()), str(uuid4())
        try:
            first = await start(loop, first_thread)
            second = await start(loop, second_thread)
            await loop.dispatch("session.delete", {"thread_id": first_thread})
            assert original.status(first["shell_id"], runtime(loop, first_thread))["status"] == "cancelled"
            assert original.status(second["shell_id"], runtime(loop, second_thread))["status"] == "running"
            await loop.dispatch("configure", request(tmp_path))
            assert loop.shell_execution is not original
            assert original.status(second["shell_id"], runtime(loop, second_thread))["status"] == "cancelled"
            current = await start(loop, first_thread)
            await loop.dispatch("shutdown", {})
            assert loop.shell_execution.status(current["shell_id"], runtime(loop, first_thread))["status"] == "cancelled"
        finally:
            await original.aclose()
            await loop.shell_execution.aclose()
    asyncio.run(run())


def test_session_delete_waits_for_active_run_cleanup_before_closing_jobs(tmp_path, monkeypatch):
    monkeypatch.setenv("VA_MODEL", "test:fake")
    loop = bridge.AgentLoop()

    async def run():
        await loop.dispatch("configure", request(tmp_path))
        thread_id = str(uuid4())
        ready = asyncio.Event()
        started = []

        async def active_run():
            try:
                ready.set()
                await asyncio.Future()
            finally:
                started.append(await start(loop, thread_id))

        task = asyncio.create_task(active_run())
        loop.active[str(uuid4())] = (thread_id, task)
        try:
            await ready.wait()
            await loop.dispatch("session.delete", {"thread_id": thread_id})
            assert task.cancelled()
            assert loop.shell_execution.status(started[0]["shell_id"], runtime(loop, thread_id))["status"] == "cancelled"
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await loop.shell_execution.aclose()
    asyncio.run(run())


@pytest.mark.parametrize("stdin", ["", "{invalid-json\n"])
def test_stdio_eof_and_protocol_error_close_background_commands(tmp_path, monkeypatch, stdin):
    monkeypatch.setenv("VA_MODEL", "test:fake")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setattr(bridge.sys, "stdin", StringIO(stdin))
    loop = bridge.AgentLoop()

    async def run():
        await loop.dispatch("configure", request(tmp_path))
        thread_id = str(uuid4())
        started = await start(loop, thread_id)
        try:
            if stdin:
                with pytest.raises(json.JSONDecodeError):
                    await loop.main()
            else:
                await loop.main()
            assert loop.shell_execution.status(started["shell_id"], runtime(loop, thread_id))["status"] == "cancelled"
        finally:
            await loop.shell_execution.aclose()
    asyncio.run(run())
