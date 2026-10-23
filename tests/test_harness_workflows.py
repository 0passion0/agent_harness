import asyncio
import json
import os
import shlex
import subprocess
import sys

import pytest
from langchain_core.exceptions import ContextOverflowError
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import Field, ValidationError

from vertical_harness.config import HarnessSettings
from vertical_harness.core import compose
from vertical_harness.core.context import RunContext
from vertical_harness.execution.shell_execution import ShellExecution


class WorkflowModel(FakeMessagesListChatModel):
    seen: list = Field(default_factory=list)
    bindings: list = Field(default_factory=list)

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        self.bindings.append((list(tools), tool_choice))
        return self

    def _generate(self, messages, *args, **kwargs):
        self.seen.append(list(messages))
        return super()._generate(messages, *args, **kwargs)


def agent_for(tmp_path, monkeypatch, responses, *, max_steps=None, mode="full"):
    model = WorkflowModel(responses=responses)
    monkeypatch.setattr(compose.ModelRouting, "init_configured_model", lambda _: model)
    settings = HarnessSettings(
        _env_file=None,
        model="test:model",
        workspace_root=tmp_path,
        store_backend="memory",
        enable_planning=False,
        max_steps=max_steps,
    )
    agent = compose.AgentComposer.build_agent(
        HarnessSettings.model_validate({**(settings).model_dump(), **{"permission_mode": mode}}),
        checkpointer=InMemorySaver(),
    )
    return agent, model


def call(name, args, identity):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": identity}])


def test_background_data_task_survives_separate_composer_turns(tmp_path, monkeypatch):
    (tmp_path / "aggregate.py").write_text(
        "import time; from pathlib import Path; time.sleep(0.2); "
        "Path('report.txt').write_text(str(sum([12, 30])))"
    )
    args = [sys.executable, "-B", "aggregate.py"]
    command = subprocess.list2cmdline(args) if os.name == "nt" else shlex.join(args)
    model = WorkflowModel(
        responses=[
            call("shell", {"command": command, "background": True}, "start"),
            AIMessage(content="Aggregation started."),
        ]
    )
    monkeypatch.setattr(compose.ModelRouting, "init_configured_model", lambda _: model)
    settings = HarnessSettings(
        _env_file=None,
        model="test:model",
        workspace_root=tmp_path,
        store_backend="memory",
        enable_planning=False,
    )
    execution = ShellExecution(tmp_path)
    context = RunContext("tenant", "user")
    config = {"configurable": {"thread_id": "report"}}

    async def run():
        nonlocal model
        try:
            async with compose.AgentComposer.aopen_agent(
                settings, shell_execution=execution
            ) as agent:
                first = await agent.ainvoke(
                    {
                        "messages": [
                            HumanMessage(content="Aggregate the figures in the background.")
                        ]
                    },
                    config,
                    context=context,
                )
            started = json.loads(
                next(
                    item.content
                    for item in first["messages"]
                    if isinstance(item, ToolMessage) and item.name == "shell"
                )
            )
            assert not execution._closed
            model = WorkflowModel(
                responses=[
                    call(
                        "shell_status", {"shell_id": started["shell_id"], "action": "wait"}, "wait"
                    ),
                    call("read_file", {"file_path": "/report.txt"}, "read"),
                    AIMessage(content="The total is 42."),
                ]
            )
            async with compose.AgentComposer.aopen_agent(
                settings, shell_execution=execution
            ) as agent:
                result = await agent.ainvoke(
                    {
                        "messages": [
                            HumanMessage(content="Wait for the result and read the report.")
                        ]
                    },
                    config,
                    context=context,
                )
            messages = [item for item in result["messages"] if isinstance(item, ToolMessage)]
            done = json.loads(
                next(item.content for item in messages if item.name == "shell_status")
            )
            assert done["status"] == "completed" and done["exit_code"] == 0
            assert "42" in next(item.content for item in messages if item.name == "read_file")
            assert (tmp_path / "report.txt").read_text() == "42"
        finally:
            await execution.aclose()

    asyncio.run(run())


@pytest.mark.parametrize("execution", ["sync", "async", "stream"])
def test_code_write_test_fail_edit_test_pass(tmp_path, monkeypatch, execution):
    args = [sys.executable, "-B", "check.py"]
    command = subprocess.list2cmdline(args) if os.name == "nt" else shlex.join(args)
    agent, model = agent_for(
        tmp_path,
        monkeypatch,
        [
            call(
                "write_file",
                {"file_path": "/maths.py", "content": "def add(a, b):\n    return a - b\n"},
                "create",
            ),
            call(
                "write_file",
                {
                    "file_path": "/check.py",
                    "content": "from maths import add\nassert add(2, 3) == 5\nprint('verified')\n",
                },
                "test",
            ),
            call("shell", {"command": command}, "fail"),
            call(
                "edit_file",
                {
                    "file_path": "/maths.py",
                    "old_string": "return a - b",
                    "new_string": "return a + b",
                },
                "fix",
            ),
            call("shell", {"command": command}, "pass"),
            AIMessage(
                content="Implemented add; check.py passed after fixing the failing assertion."
            ),
        ],
    )
    config = {"configurable": {"thread_id": execution}}
    request = {"messages": [HumanMessage(content="Implement add and verify it.")]}
    if execution == "sync":
        result = agent.invoke(request, config)
    else:

        async def run():
            if execution == "async":
                return await agent.ainvoke(request, config)
            chunks = [chunk async for chunk in agent.astream(request, config, stream_mode="values")]
            return chunks[-1]

        result = asyncio.run(run())
    shell_messages = [
        item
        for item in result["messages"]
        if isinstance(item, ToolMessage) and item.name == "shell"
    ]
    failed, passed = [json.loads(item.content) for item in shell_messages]
    assert failed["exit_code"] != 0 and "AssertionError" in failed["output"]
    assert passed["exit_code"] == 0 and "verified" in passed["output"]
    assert all((tmp_path / item["output_path"].lstrip("/")).exists() for item in (failed, passed))
    assert "return a + b" in (tmp_path / "maths.py").read_text()
    assert any(
        isinstance(item, ToolMessage) and "AssertionError" in str(item.content)
        for item in model.seen[3]
    )


@pytest.mark.parametrize("execution", ["sync", "async"])
def test_max_steps_disables_tools_and_new_user_resets_budget(tmp_path, monkeypatch, execution):
    agent, model = agent_for(
        tmp_path,
        monkeypatch,
        [
            call("write_file", {"file_path": "/first.txt", "content": "one"}, "one"),
            call("write_file", {"file_path": "/blocked.txt", "content": "blocked"}, "blocked"),
            AIMessage(content="Step limit reached; first file saved, second remains."),
            call("write_file", {"file_path": "/followup.txt", "content": "two"}, "two"),
            AIMessage(content="Follow-up saved."),
        ],
        max_steps=2,
    )
    config = {"configurable": {"thread_id": "steps"}}

    async def run():
        first = await agent.ainvoke(
            {"messages": [HumanMessage(content="Create two files")]}, config
        )
        second = await agent.ainvoke(
            {"messages": [HumanMessage(content="Now save follow-up")]}, config
        )
        return first, second

    if execution == "async":
        first, second = asyncio.run(run())
    else:
        first = agent.invoke({"messages": [HumanMessage(content="Create two files")]}, config)
        second = agent.invoke({"messages": [HumanMessage(content="Now save follow-up")]}, config)
    assert (tmp_path / "first.txt").read_text() == "one"
    assert not (tmp_path / "blocked.txt").exists()
    assert (tmp_path / "followup.txt").read_text() == "two"
    rejected = next(
        item
        for item in first["messages"]
        if isinstance(item, ToolMessage) and item.tool_call_id == "blocked"
    )
    assert rejected.status == "error" and "maximum agent steps" in rejected.content
    assert [choice for _, choice in model.bindings] == [None, "none", "none", None, "none"]
    assert all(tools for tools, _ in model.bindings)
    assert second["messages"][-1].content == "Follow-up saved."


@pytest.mark.parametrize("execution", ["sync", "async"])
def test_max_steps_stops_model_that_ignores_tool_choice(tmp_path, monkeypatch, execution):
    agent, model = agent_for(
        tmp_path,
        monkeypatch,
        [
            call(
                "write_file",
                {"file_path": "/blocked.txt", "content": "blocked"},
                f"blocked-{index}",
            )
            for index in range(20)
        ],
        max_steps=1,
    )
    config = {"configurable": {"thread_id": "ignores-limit"}, "recursion_limit": 40}
    request = {"messages": [HumanMessage(content="Keep writing indefinitely.")]}
    result = (
        asyncio.run(agent.ainvoke(request, config))
        if execution == "async"
        else agent.invoke(request, config)
    )
    assert len(model.seen) == 2
    assert "maximum agent steps" in result["messages"][-1].content.lower()
    assert not result["messages"][-1].tool_calls
    assert not (tmp_path / "blocked.txt").exists()
    assert all(
        item.status == "error" for item in result["messages"] if isinstance(item, ToolMessage)
    )


@pytest.mark.parametrize("execution", ["sync", "async"])
def test_output_limit_continues_on_native_graph_and_is_bounded(tmp_path, monkeypatch, execution):
    agent, model = agent_for(
        tmp_path,
        monkeypatch,
        [
            AIMessage(content="part one", response_metadata={"finish_reason": "length"}),
            AIMessage(content="part two", response_metadata={"stop_reason": "max_tokens"}),
            AIMessage(content="complete"),
        ],
    )
    request = {"messages": [HumanMessage(content="Explain the result")]}
    config = {"configurable": {"thread_id": "length"}}
    result = (
        asyncio.run(agent.ainvoke(request, config))
        if execution == "async"
        else agent.invoke(request, config)
    )
    assert result["messages"][-1].content == "complete"
    assert (
        sum(bool(item.additional_kwargs.get("opencode_synthetic")) for item in result["messages"])
        == 2
    )
    assert len(model.seen) == 3
    model.responses[:] = [
        AIMessage(content=f"truncated {index}", response_metadata={"finish_reason": "length"})
        for index in range(3)
    ]
    model.i = 0
    with pytest.raises(RuntimeError, match="after two output token limit continuations"):
        if execution == "async":
            asyncio.run(agent.ainvoke(request, config))
        else:
            agent.invoke(request, config)


@pytest.mark.parametrize("mode", ["read_only", "ask"])
def test_shell_obeys_existing_permission_and_approval(tmp_path, monkeypatch, mode):
    agent, _ = agent_for(
        tmp_path,
        monkeypatch,
        [
            call("shell", {"command": "echo approved"}, "shell"),
            AIMessage(content="done"),
        ],
        mode=mode,
    )
    config = {"configurable": {"thread_id": mode}}
    request = {"messages": [HumanMessage(content="Run command")]}
    if mode == "read_only":
        with pytest.raises(PermissionError, match="disabled in read_only"):
            agent.invoke(request, config)
        assert not (tmp_path / "runs").exists()
        return
    result = agent.invoke(request, config)
    if mode == "ask":
        assert result["__interrupt__"][0].value["action_requests"][0]["name"] == "shell"
        assert not (tmp_path / "runs").exists()
        result = agent.invoke(Command(resume={"decisions": [{"type": "approve"}]}), config)
        message = next(item for item in result["messages"] if isinstance(item, ToolMessage))
        assert json.loads(message.content)["exit_code"] == 0


def test_general_data_task_uses_existing_python_and_document_tools(tmp_path, monkeypatch):
    script = (
        "import csv, json\nfrom pathlib import Path\n"
        "rows = list(csv.DictReader(Path('sales.csv').open()))\n"
        "total = sum(int(row['amount']) for row in rows)\n"
        "Path('report.json').write_text(json.dumps({'total': total, 'rows': len(rows)}))\n"
        "print('report.json saved')\n"
    )
    agent, _ = agent_for(
        tmp_path,
        monkeypatch,
        [
            call(
                "write_file",
                {"file_path": "/sales.csv", "content": "item,amount\na,12\nb,18\n"},
                "data",
            ),
            call("write_file", {"file_path": "/analyse.py", "content": script}, "script"),
            call("execute_python_file", {"file_path": "/analyse.py"}, "run"),
            call("read_file", {"file_path": "/report.json"}, "verify"),
            AIMessage(content="Verified report.json: 2 rows, total 30."),
        ],
    )
    result = asyncio.run(
        agent.ainvoke(
            {"messages": [HumanMessage(content="Analyse sales and save a JSON report.")]},
            {"configurable": {"thread_id": "general-data"}},
        )
    )
    assert json.loads((tmp_path / "report.json").read_text()) == {"total": 30, "rows": 2}
    messages = [item for item in result["messages"] if isinstance(item, ToolMessage)]
    run = next(item for item in messages if item.name == "execute_python_file")
    assert json.loads(run.content)["exit_code"] == 0
    assert '"total": 30' in next(item for item in messages if item.name == "read_file").content


def test_step_configuration_and_general_task_prompt():
    with pytest.raises(ValidationError):
        HarnessSettings(_env_file=None, model="test:model", max_steps=0)
    for task in ("research", "documents", "data analysis", "automation", "software"):
        assert task in compose.BASE_PROMPT
    assert "For code tasks" in compose.BASE_PROMPT


def test_root_instructions_refresh_through_real_graph(tmp_path, monkeypatch):
    (tmp_path / "AGENTS.md").write_text("INSTRUCTION_FIRST", encoding="utf-8")
    agent, model = agent_for(
        tmp_path,
        monkeypatch,
        [
            call(
                "edit_file",
                {
                    "file_path": "/AGENTS.md",
                    "old_string": "INSTRUCTION_FIRST",
                    "new_string": "INSTRUCTION_SECOND",
                },
                "instruction",
            ),
            AIMessage(content="Updated instructions verified."),
        ],
    )
    agent.invoke(
        {"messages": [HumanMessage(content="Update instructions")]},
        {"configurable": {"thread_id": "instructions"}},
    )
    assert "INSTRUCTION_FIRST" in str(model.seen[0][0].content)
    assert "INSTRUCTION_SECOND" in str(model.seen[1][0].content)
    assert "INSTRUCTION_FIRST" not in str(model.seen[1][0].content)


def test_root_instructions_are_included_in_context_budget(tmp_path, monkeypatch):
    (tmp_path / "AGENTS.md").write_text("long root instruction " * 3000, encoding="utf-8")
    model = WorkflowModel(responses=[AIMessage(content="must not run")])
    monkeypatch.setattr(compose.ModelRouting, "init_configured_model", lambda _: model)
    agent = compose.AgentComposer.build_agent(
        HarnessSettings.model_validate(
            {
                **(
                    HarnessSettings(
                        _env_file=None,
                        model="test:model",
                        workspace_root=tmp_path,
                        enable_planning=False,
                    )
                ).model_dump(),
                **{"max_context_tokens": 4000},
            }
        ),
    )
    with pytest.raises(ContextOverflowError) as failure:
        agent.invoke(
            {"messages": [HumanMessage(content="Hello")]}, {"configurable": {"thread_id": "budget"}}
        )
    assert "history was not sent to the model" in str(failure.value.__cause__)
    assert model.seen == []
