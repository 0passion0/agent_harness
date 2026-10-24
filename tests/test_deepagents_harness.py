import asyncio
from contextlib import aclosing

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command
from pydantic import Field

from vertical_harness.core import compose
from vertical_harness.core.compose import AgentComposer
from vertical_harness.langchain.streaming import AgentStreaming
from vertical_harness.langchain.tool_details import ToolDetails
from vertical_harness.models import HarnessSettings


class ToolModel(FakeMessagesListChatModel):
    bound_tools: list[set[str]] = Field(default_factory=list)
    seen_messages: list = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        self.bound_tools.append({tool.name for tool in tools})
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen_messages.append(messages)
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def make_agent(tmp_path, monkeypatch, mode, tool_name="write_file", args=None):
    model = ToolModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "delegate",
                        "name": "task",
                        "args": {
                            "description": "Check the workspace",
                            "subagent_type": "general-purpose",
                        },
                    }
                ],
            ),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "child-tool",
                        "name": tool_name,
                        "args": args
                        if args is not None
                        else {"file_path": "/result.txt", "content": "verified"},
                    }
                ],
            ),
            AIMessage(content="Child finished"),
            AIMessage(content="All done"),
        ]
    )
    monkeypatch.setattr(compose.ModelRouting, "init_configured_model", lambda _settings: model)
    settings = HarnessSettings(
        _env_file=None,
        model="test:deepagents",
        workspace_root=tmp_path,
        store_backend="memory",
        enable_planning=False,
    )
    return AgentComposer.build_agent(
        HarnessSettings.model_validate({**(settings).model_dump(), **{"permission_mode": mode}})
    ), model


def test_deepagents_delegates_with_the_existing_tool_set(tmp_path, monkeypatch):
    agent, model = make_agent(tmp_path, monkeypatch, "full")
    result = agent.invoke(
        {"messages": [HumanMessage(content="Delegate the workspace check")]},
        {"configurable": {"thread_id": "delegation"}},
    )
    assert result["messages"][-1].content == "All done"
    assert (tmp_path / "result.txt").read_text() == "verified"
    assert agent.config["metadata"]["ls_integration"] == "deepagents"
    assert "task" in model.bound_tools[0]
    assert all("execute" not in tools and "write_todos" not in tools for tools in model.bound_tools)
    assert "write_file" in model.bound_tools[1]


@pytest.mark.parametrize("asynchronous", [False, True])
def test_global_and_project_instructions_and_skills_reach_child(
    tmp_path, monkeypatch, asynchronous
):
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    system = home / ".agent-harness"
    for root, name, description in [
        (system, "shared", "global shared skill"),
        (system, "global-only", "global unique skill"),
        (workspace / ".agents", "shared", "project shared skill"),
    ]:
        skill = root / "skills" / name / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text(
            f"---\nname: {name}\ndescription: {description}\n---\n{description} body",
            encoding="utf-8",
        )
    (system / "AGENTS.md").write_text("global instruction marker", encoding="utf-8")
    (workspace / "AGENTS.md").write_text("project instruction marker", encoding="utf-8")
    monkeypatch.setattr(compose.Path, "home", lambda: home)
    agent, model = make_agent(
        workspace,
        monkeypatch,
        "full",
        tool_name="read_file",
        args={"file_path": "/.agent-harness/skills/global-only/SKILL.md"},
    )
    inputs = {"messages": [HumanMessage(content="Use the global skill in a child task")]}
    config = {"configurable": {"thread_id": "skills"}}
    result = (
        asyncio.run(agent.ainvoke(inputs, config)) if asynchronous else agent.invoke(inputs, config)
    )
    assert result["messages"][-1].content == "All done"
    skills = {skill["name"]: skill for skill in agent.get_state(config).values["skills_metadata"]}
    assert skills["shared"]["path"] == "/.agents/skills/shared/SKILL.md"
    assert skills["shared"]["description"] == "project shared skill"
    assert skills["global-only"]["path"] == "/.agent-harness/skills/global-only/SKILL.md"
    for messages in model.seen_messages:
        prompt = messages[0].text
        assert "global instruction marker" in prompt
        assert "project instruction marker" in prompt
        assert "global unique skill" in prompt
        assert "project shared skill" in prompt
        assert "global shared skill" not in prompt
    assert any(
        message.type == "tool" and "global unique skill body" in message.text
        for messages in model.seen_messages
        for message in messages
    )


@pytest.mark.parametrize("asynchronous", [False, True])
def test_global_skills_are_read_only(tmp_path, monkeypatch, asynchronous):
    home = tmp_path / "home"
    skill = home / ".agent-harness" / "skills" / "example" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    content = "---\nname: example\ndescription: Example skill\n---\nOriginal skill"
    skill.write_text(content, encoding="utf-8")
    monkeypatch.setattr(compose.Path, "home", lambda: home)
    workspace = tmp_path / "workspace"
    agent, model = make_agent(
        workspace,
        monkeypatch,
        "full",
        args={"file_path": "/.agent-harness/skills/example/SKILL.md", "content": "changed"},
    )
    inputs = {"messages": [HumanMessage(content="Try writing a global skill")]}
    config = {"configurable": {"thread_id": "skills-read-only"}}
    if asynchronous:
        asyncio.run(agent.ainvoke(inputs, config))
    else:
        agent.invoke(inputs, config)
    assert skill.read_text(encoding="utf-8") == content
    assert not (workspace / ".agent-harness").exists()
    assert any(
        message.type == "tool" and "permission denied" in message.text
        for messages in model.seen_messages
        for message in messages
    )


@pytest.mark.parametrize(
    "file_path",
    ["/.agent-harness/skills/example/.env", "/.agent-harness/skills/../private.txt"],
)
def test_global_skill_reads_reject_sensitive_files_and_traversal(tmp_path, monkeypatch, file_path):
    home = tmp_path / "home"
    system = home / ".agent-harness"
    skill = system / "skills" / "example"
    skill.mkdir(parents=True)
    (skill / ".env").write_text("sensitive marker", encoding="utf-8")
    (system / "private.txt").write_text("sensitive marker", encoding="utf-8")
    monkeypatch.setattr(compose.Path, "home", lambda: home)
    agent, model = make_agent(
        tmp_path / "workspace",
        monkeypatch,
        "full",
        tool_name="read_file",
        args={"file_path": file_path},
    )
    agent.invoke(
        {"messages": [HumanMessage(content="Try reading a restricted global file")]},
        {"configurable": {"thread_id": "skill-read-denied"}},
    )
    reads = [
        message
        for messages in model.seen_messages
        for message in messages
        if message.type == "tool" and message.name == "read_file"
    ]
    assert reads and all(message.status == "error" for message in reads)
    assert all("sensitive marker" not in message.text for message in reads)


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_delegated_writes_require_and_resume_desktop_approval(tmp_path, monkeypatch, decision):
    agent, _model = make_agent(tmp_path, monkeypatch, "ask")
    config = {"configurable": {"thread_id": "delegated-approval"}}
    pending = agent.invoke({"messages": [HumanMessage(content="Delegate a file update")]}, config)
    assert pending["__interrupt__"][0].value.items() >= {
        "type": "tool_approval", "tool_call_id": "child-tool", "name": "write_file",
        "args": {"file_path": "/result.txt", "content": "verified"},
    }.items()
    assert not (tmp_path / "result.txt").exists()
    resumed = agent.invoke(Command(resume={"type": decision}), config)
    assert resumed["messages"][-1].content == "All done"
    assert (tmp_path / "result.txt").exists() == (decision == "approve")


def test_read_only_blocks_delegation_before_child_execution(tmp_path, monkeypatch):
    agent, model = make_agent(tmp_path, monkeypatch, "read_only")
    with pytest.raises(PermissionError, match="task is disabled in read_only mode"):
        agent.invoke(
            {"messages": [HumanMessage(content="Delegate a file update")]},
            {"configurable": {"thread_id": "readonly-delegation"}},
        )
    assert len(model.seen_messages) == 1
    assert not (tmp_path / "result.txt").exists()


def test_delegation_cannot_read_sensitive_workspace_files(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("SECRET_SENTINEL")
    agent, model = make_agent(tmp_path, monkeypatch, "full", "read_file", {"file_path": "/.env"})
    agent.invoke(
        {"messages": [HumanMessage(content="Delegate a workspace inspection")]},
        {"configurable": {"thread_id": "sensitive-delegation"}},
    )
    assert "SECRET_SENTINEL" not in repr(model.seen_messages)
    assert "denied" in repr(model.seen_messages).lower()


def test_delegated_approval_streams_to_desktop_and_resumes(tmp_path, monkeypatch):
    agent, _model = make_agent(tmp_path, monkeypatch, "ask")
    details = ToolDetails()
    config = {"configurable": {"thread_id": "streaming-delegation"}, "callbacks": [details]}

    async def run():
        async with aclosing(
            AgentStreaming.astream_agent(
                agent, {"messages": [HumanMessage(content="Delegate a file update")]}, config=config
            )
        ) as events:
            pending = [event async for event in events]
        assert pending[-1].type == "run.interrupted"
        assert pending[-1].data["interrupts"][0]["value"]["type"] == "tool_approval"
        assert not (tmp_path / "result.txt").exists()
        async with aclosing(
            AgentStreaming.astream_agent(
                agent, Command(resume={"type": "approve"}), config=config
            )
        ) as events:
            resumed = [event async for event in events]
        assert resumed[-1].type == "run.completed"
        assert resumed[-1].data["output"] == "All done"
        assert (tmp_path / "result.txt").read_text() == "verified"
        task = next(
            event for event in resumed if event.type == "tool.completed" and event.name == "task"
        )
        assert details.records[task.operation_id]["output"] == "Child finished"

    asyncio.run(run())
