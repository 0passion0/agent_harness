import asyncio
import json
import os
import shlex
import subprocess
import sys
import time
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Event

import pytest
from langchain.agents import create_agent
from langchain.tools import ToolRuntime
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import ToolException

from vertical_harness.core.context import RunContext
from vertical_harness.execution.process_control import ProcessControl
from vertical_harness.execution.shell_execution import ShellExecution


def command(script):
    args = [sys.executable, "-u", script]
    return subprocess.list2cmdline(args) if os.name == "nt" else shlex.join(args)


def runtime(thread="session", tenant="tenant", user="user"):
    return ToolRuntime(
        state={},
        context=RunContext(tenant, user),
        config={"configurable": {"thread_id": thread}},
        stream_writer=lambda event: None,
        tool_call_id="shell-call",
        store=None,
    )


def test_background_returns_immediately_and_survives_invocation_loop(tmp_path):
    (tmp_path / "aggregate.py").write_text(
        "from pathlib import Path; import time; "
        "print('aggregating', flush=True); time.sleep(0.6); "
        "Path('total.txt').write_text(str(sum([12, 30]))); print('total=42')"
    )
    execution = ShellExecution(tmp_path)
    try:
        started = time.monotonic()
        result = asyncio.run(
            execution.aexecute(command("aggregate.py"), background=True, runtime=runtime())
        )
        assert time.monotonic() - started < 0.6
        assert result["status"] == "running"
        assert (tmp_path / result["output_path"].removeprefix("/")).exists()
        done = execution.status(result["shell_id"], runtime(), "wait")
        assert done["status"] == "completed"
        assert done["exit_code"] == 0
        assert not done["timed_out"]
        assert "total=42" in done["output"]
        assert (tmp_path / "total.txt").read_text() == "42"
    finally:
        execution.close()


@pytest.mark.parametrize(
    "other", [runtime(thread="other"), runtime(tenant="other"), runtime(user="other")]
)
def test_background_rejects_other_sessions_and_identities(tmp_path, other):
    execution = ShellExecution(tmp_path)
    try:
        result = execution.execute(command("missing.py"), background=True, runtime=runtime())
        for action in ("status", "wait", "cancel", "output", "timeout", "remove"):
            with pytest.raises(ToolException, match="not found in this session"):
                execution.status(result["shell_id"], other, action, timeout=100)
        assert execution.status(runtime=other, action="list") == {"shells": []}
        assert execution.status(result["shell_id"], runtime(), "wait")["exit_code"] != 0
    finally:
        execution.close()


def test_background_validation_precedes_spawn_and_output(tmp_path):
    execution = ShellExecution(tmp_path)
    with pytest.raises(ToolException, match="trusted run context"):
        execution.execute("echo must-not-run", background=True)
    without_session = runtime()
    without_session.config = {}
    with pytest.raises(ToolException, match="thread_id"):
        execution.execute("echo must-not-run", background=True, runtime=without_session)
    with pytest.raises(ToolException, match="inside the workspace"):
        execution.execute("echo must-not-run", workdir="..", background=True, runtime=runtime())
    assert not (tmp_path / "runs").exists()


def test_background_timeout_keeps_output_and_nonzero_exit(tmp_path):
    (tmp_path / "slow.py").write_text("import time; print('started'); time.sleep(60)")
    execution = ShellExecution(tmp_path)
    try:
        result = execution.execute(
            command("slow.py"), timeout=500, background=True, runtime=runtime()
        )
        done = execution.status(result["shell_id"], runtime(), "wait")
        assert done["status"] == "completed"
        assert done["timed_out"]
        assert done["exit_code"] != 0
        assert "started" in done["output"]
    finally:
        execution.close()


@pytest.mark.parametrize("action", ["cancel", "close"])
def test_background_cancel_and_owner_close_stop_descendants(tmp_path, action):
    (tmp_path / "child.py").write_text(
        "from pathlib import Path; import time; "
        "Path('ready').touch(); time.sleep(1); Path('escaped').touch()"
    )
    (tmp_path / "parent.py").write_text(
        "import subprocess, sys, time; "
        "subprocess.Popen([sys.executable, 'child.py']); time.sleep(60)"
    )
    execution = ShellExecution(tmp_path)
    try:
        result = execution.execute(command("parent.py"), background=True, runtime=runtime())
        deadline = time.monotonic() + 10
        while not (tmp_path / "ready").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert (tmp_path / "ready").exists()
        if action == "cancel":
            done = execution.status(result["shell_id"], runtime(), "cancel")
            assert done["status"] == "cancelled"
            assert done["exit_code"] != 0
        else:
            execution.close()
            with pytest.raises(ToolException, match="closed"):
                execution.execute("echo must-not-run", background=True, runtime=runtime())
        time.sleep(1.1)
        assert not (tmp_path / "escaped").exists()
    finally:
        execution.close()


def test_background_graph_tools_use_injected_owner_for_ordinary_task(tmp_path):
    class Model(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, **kwargs):
            if isinstance(messages[-1], ToolMessage) and messages[-1].name == "shell":
                result = json.loads(messages[-1].content)
                self.responses[1] = AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": "wait",
                            "name": "shell_status",
                            "args": {"shell_id": result["shell_id"], "action": "wait"},
                        }
                    ],
                )
            return super()._generate(messages, **kwargs)

    (tmp_path / "report.py").write_text("print('Meeting attendance: 42')")
    execution = ShellExecution(tmp_path)
    try:
        tools = execution.tools()
        assert "runtime" not in tools[0].tool_call_schema.model_fields
        agent = create_agent(
            model=Model(
                responses=[
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "id": "launch",
                                "name": "shell",
                                "args": {
                                    "command": command("report.py"),
                                    "background": True,
                                },
                            }
                        ],
                    ),
                    AIMessage(content="placeholder"),
                    AIMessage(content="Attendance is 42."),
                ]
            ),
            tools=tools,
            context_schema=RunContext,
        )
        result = agent.invoke(
            {"messages": [HumanMessage(content="Prepare a meeting attendance report.")]},
            config={"configurable": {"thread_id": "report-session"}},
            context=RunContext("tenant", "user"),
        )
        messages = [message for message in result["messages"] if isinstance(message, ToolMessage)]
        assert all(message.status == "success" for message in messages)
        assert "Meeting attendance: 42" in messages[-1].content
        assert result["messages"][-1].content == "Attendance is 42."
    finally:
        execution.close()


def test_background_worker_failures_propagate_to_wait_and_close(tmp_path, monkeypatch):
    def broken_capture(path):
        raise OSError("capture unavailable")

    execution = ShellExecution(tmp_path)
    monkeypatch.setattr(ShellExecution, "_capture", staticmethod(broken_capture))
    result = execution.execute(command("missing.py"), background=True, runtime=runtime())
    with pytest.raises(OSError, match="capture unavailable"):
        execution.status(result["shell_id"], runtime(), "wait")
    with pytest.raises(OSError, match="capture unavailable"):
        execution.close()


def test_background_retains_only_25_completed_shells_but_keeps_logs(tmp_path, monkeypatch):
    (tmp_path / "report.py").write_text("print('saved report')")
    execution = ShellExecution(tmp_path)
    retained_at_completion = []

    class TrackedFuture(Future):
        def set_result(self, result):
            retained_at_completion.append(len(execution._jobs))
            return super().set_result(result)

    monkeypatch.setattr("vertical_harness.execution.shell_execution.Future", TrackedFuture)
    try:
        results = []
        for _ in range(26):
            result = execution.execute(command("report.py"), background=True, runtime=runtime())
            execution.status(result["shell_id"], runtime(), "wait")
            results.append(result)
        with pytest.raises(ToolException, match="not found"):
            execution.status(results[0]["shell_id"], runtime())
        assert execution.status(results[1]["shell_id"], runtime())["status"] == "completed"
        first_output = tmp_path / results[0]["output_path"].removeprefix("/")
        assert first_output.read_text().strip() == "saved report"
        assert max(retained_at_completion) == 25
    finally:
        execution.close()


def test_background_session_cleanup_leaves_other_session_running(tmp_path):
    (tmp_path / "slow.py").write_text("import time; time.sleep(60)")
    execution = ShellExecution(tmp_path)
    try:
        first = execution.execute(command("slow.py"), background=True, runtime=runtime())
        second = execution.execute(
            command("slow.py"), background=True, runtime=runtime(thread="other")
        )
        asyncio.run(execution.aclose_session("tenant", "user", "session"))
        assert execution.status(first["shell_id"], runtime())["status"] == "cancelled"
        assert execution.status(second["shell_id"], runtime(thread="other"))["status"] == "running"
    finally:
        asyncio.run(execution.aclose())


def test_background_output_pages_use_bytes_and_independent_utf8_decoding(tmp_path):
    data = "Aé中🙂Z".encode()
    (tmp_path / "report.py").write_text(
        f"import sys; sys.stdout.buffer.write(bytes.fromhex('{data.hex()}'))"
    )
    execution = ShellExecution(tmp_path)
    try:
        started = execution.execute(command("report.py"), background=True, runtime=runtime())
        shell_id = started["shell_id"]
        execution.status(shell_id, runtime(), "wait")
        for cursor in range(0, len(data), 2):
            page = execution.status(shell_id, runtime(), "output", cursor=cursor, limit=2)
            assert page == {
                "shell_id": shell_id,
                "output": data[cursor : cursor + 2].decode("utf-8", errors="replace"),
                "cursor": min(cursor + 2, len(data)),
                "size": len(data),
                "truncated": False,
            }
        assert execution.status(shell_id, runtime(), "output")["output"] == data.decode()
        assert execution.status(shell_id, runtime(), "output", cursor=1, limit=0)["cursor"] == 1
        end = execution.status(shell_id, runtime(), "output", cursor=100)
        assert end["output"] == ""
        assert end["cursor"] == end["size"] == len(data)
    finally:
        execution.close()


def test_background_timeout_can_be_cleared_and_reset_from_now(tmp_path):
    (tmp_path / "slow.py").write_text("import time; print('started'); time.sleep(60)")
    execution = ShellExecution(tmp_path)
    try:
        started = execution.execute(
            command("slow.py"), timeout=300, background=True, runtime=runtime()
        )
        shell_id = started["shell_id"]
        with pytest.raises(ToolException, match="requires timeout"):
            execution.status(shell_id, runtime(), "timeout")
        execution.status(shell_id, runtime(), "timeout", timeout=0)
        time.sleep(0.4)
        assert execution.status(shell_id, runtime())["status"] == "running"
        assert [
            shell["shell_id"]
            for shell in execution.status(runtime=runtime(), action="list")["shells"]
        ] == [shell_id]
        execution.status(shell_id, runtime(), "timeout", timeout=5000)
        started = time.monotonic()
        execution.status(shell_id, runtime(), "timeout", timeout=100)
        done = execution.status(shell_id, runtime(), "wait")
        assert time.monotonic() - started < 3
        assert done["timed_out"]
        assert execution.status(shell_id, runtime(), "timeout", timeout=0) == done
        assert execution.status(runtime=runtime(), action="list") == {"shells": []}
    finally:
        execution.close()


@pytest.mark.parametrize("cleanup", ["close", "close_session"])
def test_background_remove_unblocks_wait_and_can_overlap_owner_cleanup(
    tmp_path, monkeypatch, cleanup
):
    (tmp_path / "slow.py").write_text("import time; print('started'); time.sleep(60)")
    execution = ShellExecution(tmp_path)
    started = execution.execute(command("slow.py"), background=True, runtime=runtime())
    shell_id = started["shell_id"]
    job = execution._jobs[shell_id]
    waiting = Event()
    removing = Event()
    closing = Event()
    release = Event()
    original_result = job.future.result
    original_stop = ProcessControl.terminate
    original_close_jobs = execution._close_jobs

    def wait_result(*args, **kwargs):
        waiting.set()
        return original_result(*args, **kwargs)

    def stop(target):
        if job.removed:
            removing.set()
            assert release.wait(10)
        original_stop(target)

    def close_jobs(jobs):
        closing.set()
        original_close_jobs(jobs)

    monkeypatch.setattr(job.future, "result", wait_result)
    monkeypatch.setattr(ProcessControl, "terminate", staticmethod(stop))
    monkeypatch.setattr(execution, "_close_jobs", close_jobs)
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            waiter = pool.submit(execution.status, shell_id, runtime(), "wait")
            assert waiting.wait(10)
            remover = pool.submit(execution.status, shell_id, runtime(), "remove")
            assert removing.wait(10)
            owner_cleanup = (
                pool.submit(execution.close)
                if cleanup == "close"
                else pool.submit(execution.close_session, "tenant", "user", "session")
            )
            assert closing.wait(10)
            release.set()
            assert remover.result(timeout=10) == {"shell_id": shell_id, "status": "removed"}
            owner_cleanup.result(timeout=10)
            with pytest.raises(ToolException, match="not found"):
                waiter.result(timeout=10)
        assert job.process.poll() is not None
        assert not (tmp_path / started["output_path"].removeprefix("/")).exists()
        assert shell_id not in execution._completed
        with pytest.raises(ToolException, match="not found"):
            execution.status(shell_id, runtime())
        assert execution.status(runtime=runtime(), action="list") == {"shells": []}
    finally:
        release.set()
        execution.close()


def test_background_remove_completed_shell_deletes_capture_and_retention_entry(tmp_path):
    (tmp_path / "report.py").write_text("print('saved report')")
    execution = ShellExecution(tmp_path)
    try:
        started = execution.execute(command("report.py"), background=True, runtime=runtime())
        shell_id = started["shell_id"]
        execution.status(shell_id, runtime(), "wait")
        assert shell_id in execution._completed
        execution.status(shell_id, runtime(), "remove")
        assert shell_id not in execution._completed
        assert not (tmp_path / started["output_path"].removeprefix("/")).exists()
        with pytest.raises(ToolException, match="not found"):
            execution.status(shell_id, runtime(), "output")
    finally:
        execution.close()


def test_background_remove_refuses_capture_outside_its_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("must stay")
    execution = ShellExecution(workspace)
    started = execution.execute(command("missing.py"), background=True, runtime=runtime())
    shell_id = started["shell_id"]
    execution.status(shell_id, runtime(), "wait")
    job = execution._jobs[shell_id]
    original_path = job.path
    try:
        job.path = outside
        with pytest.raises(ToolException, match="inside the workspace"):
            execution.status(shell_id, runtime(), "remove")
        assert outside.read_text() == "must stay"
        assert not job.removed
    finally:
        job.path = original_path
        execution.close()
