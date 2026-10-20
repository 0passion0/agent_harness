from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command, interrupt

from vertical_harness import (
    BuildServices,
    HarnessSettings,
    RunContext,
    astream_agent,
    build_agent,
    make_checkpoint_config,
)
from vertical_harness.desktop_runtime import (
    ContextLimitMiddleware,
    DesktopPermissionMiddleware,
    DesktopPlugin,
    aopen_desktop_agent,
    ask_user,
    attachment_message,
    configured_settings,
    raw_message,
    usage_estimate,
)


def options():
    return {
        "model_code": "deepseek:deepseek-chat",
        "runtimes": {"python": "python.exe", "node": "node.exe"},
        "permission_mode": "ask",
        "max_context_tokens": 128_000,
        "agent_content": "instructions",
        "skills": [{"id": "demo", "content": "skill text", "source": "project"}],
        "mcp_servers": {},
        "enabled_plugins": [],
    }


@pytest.mark.parametrize("choices,answer", [(None, "研究目标"), (["摘要", "完整报告"], "摘要"), (["摘要"], "自定义双语报告")])
def test_ask_user_interrupts_and_resumes_with_choice_or_custom_text(choices, answer):
    def step(_state):
        result = ask_user.invoke({"question": "需要什么报告？", "options": choices})
        return {"messages": [AIMessage(content=result)]}

    graph = StateGraph(MessagesState)
    graph.add_node("question", step)
    graph.add_edge(START, "question")
    graph.add_edge("question", END)
    app = graph.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "question"}}
    paused = app.invoke({"messages": []}, config)
    pending = paused["__interrupt__"][0]
    assert pending.value == {"kind": "question", "question": "需要什么报告？", "options": choices or []}
    resumed = app.invoke(Command(resume={pending.id: answer}), config)
    assert resumed["messages"][-1].content == answer


def test_ask_user_rejects_blank_questions_and_options():
    for params in ({"question": " "}, {"question": "format?", "options": [" "]}):
        with pytest.raises(ValueError, match="must not be blank"):
            ask_user.invoke(params)


@pytest.mark.parametrize("mode", ["read_only", "ask", "full"])
def test_composed_desktop_agent_can_ask_questions_in_every_permission_mode(tmp_path, monkeypatch, mode):
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

    from vertical_harness.engine import compose

    class Model(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            assert any(getattr(item, "name", None) == "ask_user" for item in tools)
            return self

    monkeypatch.setattr(compose, "init_configured_model", lambda _settings: Model(responses=[
        AIMessage(content="", tool_calls=[{"id": "ask", "name": "ask_user", "args": {
            "question": "选择格式", "options": ["摘要", "完整报告"],
        }}]), AIMessage(content="done"),
    ]))
    selected = {**options(), "permission_mode": mode}
    settings = HarnessSettings(_env_file=None, model="test:questions", workspace_root=tmp_path,
                               store_backend="memory", enable_memory=False)
    app = build_agent(settings, store=InMemoryStore(), checkpointer=InMemorySaver(),
                      mcp_tools=[], plugins=[DesktopPlugin(selected)])

    async def run():
        config = {"configurable": {"thread_id": "questions"}}
        paused = await app.ainvoke({"messages": [HumanMessage(content="write report")]}, config)
        pending = paused["__interrupt__"][0]
        assert pending.value["options"] == ["摘要", "完整报告"]
        resumed = await app.ainvoke(Command(resume={pending.id: "自定义双语摘要"}), config)
        assert any(isinstance(message, ToolMessage) and message.content == "自定义双语摘要"
                   for message in resumed["messages"])

    asyncio.run(run())


def test_configured_settings_preserve_provider_model_and_runtime_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("VA_MODEL", "deepseek:configured")
    settings, desktop = configured_settings(
        {
            "workspace_root": str(tmp_path / "workspace"),
            "runtime_data_dir": str(tmp_path / "runtime"),
            "options": options(),
        }
    )
    assert settings.model == "deepseek:deepseek-chat"
    assert settings.resolved_workspace_root == (tmp_path / "workspace").resolve()
    assert desktop["runtimes"] == {"python": "python.exe", "node": "node.exe"}


@pytest.mark.parametrize("maximum", [0, 475_001])
def test_configured_settings_rejects_context_outside_475k_budget(tmp_path, maximum):
    with pytest.raises(ValueError, match="between 1 and 475000"):
        configured_settings({
            "workspace_root": str(tmp_path / "workspace"),
            "runtime_data_dir": str(tmp_path / "runtime"),
            "options": {**options(), "max_context_tokens": maximum},
        })


def test_attachments_inside_workspace_are_referenced_in_place(tmp_path):
    workspace = tmp_path / "workspace"
    original = workspace / "report.csv"
    original.parent.mkdir()
    original.write_text("a,b\n1,2\n", encoding="utf-8")
    message = attachment_message(
        workspace,
        "Analyze this",
        [
            {
                "name": "report.csv",
                "path": str(original),
                "media_type": "text/csv",
            }
        ],
    )
    assert message[0]["text"] == "Analyze this"
    assert "Path: /report.csv" in message[1]["text"]


def test_snapshot_keeps_multimodal_content_and_marks_usage_estimate():
    message = AIMessage(
        content=[{"type": "text", "text": "done"}],
        usage_metadata={
            "input_tokens": 8,
            "output_tokens": 3,
            "total_tokens": 11,
        },
    )
    dumped = raw_message(message)
    assert dumped["content"] == [{"type": "text", "text": "done"}]
    assert dumped["usage_metadata"]["total_tokens"] == 11
    assert usage_estimate([message], "deepseek:deepseek-chat")["used_tokens"] > 0


def test_context_usage_counts_actual_request_and_normalizes_reported_usage():
    messages = [
        SystemMessage(content=[
            {"type": "text", "text": "Real system instructions"},
            {"type": "text", "text": "## Skills System\nA skill catalog"},
            {"type": "text", "text": "User memory (untrusted reference data): preferences"},
        ]),
        HumanMessage(content="Check this file"),
        AIMessage(content="", tool_calls=[{"id": "read", "name": "read_file", "args": {"file_path": "/skills/demo/SKILL.md"}}]),
        ToolMessage(content="Loaded skill instructions", tool_call_id="read"),
        AIMessage(content="Done"),
    ]
    report = usage_estimate([], "test:model", 100, request_messages=messages,
                            tools=[{"name": "read_file", "description": "Read a file"}], reported_tokens=150)
    sections = report["sections"].values()
    assert all(section["tokens"] > 0 for section in sections)
    assert sum(section["tokens"] for section in sections) == 150
    assert sum(section["ratio_of_used"] for section in sections) == pytest.approx(1)
    assert report["used_ratio"] == 1.5
    assert report["remaining_tokens"] == report["remaining_ratio"] == 0
    pending = usage_estimate([], "test:model", options=options())
    assert pending["used_tokens"] == 0
    assert pending["sections"]["skills"]["tokens"] is None


def test_enabled_plugin_skills_refresh_in_existing_chat_and_stay_off_disk(tmp_path, monkeypatch):
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

    from vertical_harness import DomainPlugin, PluginContribution, PluginManifest, SkillAsset
    from vertical_harness.desktop import ToolDetails
    from vertical_harness.engine import compose

    skill_path = "/skills/demo-domain-guide/SKILL.md"
    skill_text = "---\nname: domain-guide\ndescription: Domain-specific help\n---\nRead these domain instructions."

    class Plugin(DomainPlugin):
        manifest = PluginManifest(plugin_id="demo.domain", version="1.0.0", description="Domain test plugin")

        def build(self, services):
            return PluginContribution(skills=[SkillAsset(relative_path="guide/SKILL.md", content=skill_text)])

    captured = []

    class Model(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            captured.append(messages)
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    monkeypatch.setattr(compose, "init_configured_model", lambda settings: Model(responses=[
        AIMessage(content="", tool_calls=[{"id": "read", "name": "read_file", "args": {"file_path": skill_path}}]),
        AIMessage(content="Done", usage_metadata={"input_tokens": 800, "output_tokens": 20, "total_tokens": 820}),
    ]))
    selected = options()
    selected.update(permission_mode="full", skills=[], max_context_tokens=128_000)
    settings = HarnessSettings(_env_file=None, model="test:model", workspace_root=tmp_path, store_backend="memory", enable_memory=False)
    saver, store = InMemorySaver(), InMemoryStore()
    callback = ToolDetails()

    async def run():
        for plugins in ([Plugin()], [], [Plugin()]):
            app = build_agent(settings, store=store, checkpointer=saver, mcp_tools=[], plugins=[*plugins, DesktopPlugin(selected)])
            result = await app.ainvoke({"messages": [HumanMessage(content="Help with domain")]},
                                       {"configurable": {"thread_id": "same-chat"}, "callbacks": [callback]})
            state = await app.aget_state({"configurable": {"thread_id": "same-chat"}})
            assert bool(state.values.get("skills_metadata")) == bool(plugins)
            if plugins:
                assert any(isinstance(m, ToolMessage) and skill_text in m.content for m in result["messages"])

    asyncio.run(run())
    assert "domain-guide" in str(captured[0][0].content)
    assert not (tmp_path / "skills").exists()
    assert callback.context_reports
    assert list(callback.context_reports.values())[-1]["used_tokens"] == 820


def test_attachment_rejects_relative_path(tmp_path):
    with pytest.raises(ValueError, match="absolute"):
        attachment_message(
            tmp_path,
            "text",
            [
                {
                    "name": "file",
                    "path": "relative.txt",
                    "media_type": "text/plain",
                }
            ],
        )


def test_read_only_mode_blocks_builtin_write_tools(tmp_path):
    selected = options()
    selected["permission_mode"] = "read_only"
    contribution = DesktopPlugin(selected).build(BuildServices(tmp_path))
    assert contribution.tools == []
    permission = next(
        item for item in contribution.middleware if isinstance(item, DesktopPermissionMiddleware)
    )
    from types import SimpleNamespace

    with pytest.raises(PermissionError, match="disabled in read_only"):
        permission.wrap_tool_call(
            SimpleNamespace(tool=SimpleNamespace(name="write_file")), lambda request: request
        )


def test_context_limit_rejects_oversized_history():
    from langchain.agents.middleware import ModelRequest
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

    middleware = ContextLimitMiddleware(1, "", [])
    request = ModelRequest(
        model=FakeMessagesListChatModel(responses=[]),
        messages=[HumanMessage(content="too many chars")],
        tools=[],
    )
    with pytest.raises(ValueError, match="exceeds configured max_context_tokens"):
        middleware.wrap_model_call(request, lambda _request: None)

    async def reject_async_request():
        with pytest.raises(ValueError, match="exceeds configured max_context_tokens"):
            await middleware.awrap_model_call(request, lambda _request: None)

    asyncio.run(reject_async_request())


@pytest.mark.parametrize("use_async", [False, True])
@pytest.mark.parametrize("section", ["system", "tools"])
def test_context_limit_counts_actual_system_prompt_and_tool_schemas(use_async, section):
    from langchain.agents.middleware import ModelRequest
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

    request = ModelRequest(
        model=FakeMessagesListChatModel(responses=[]),
        messages=[],
        system_message=SystemMessage(content="instructions " * 1000) if section == "system" else None,
        tools=[{
            "name": "inspect",
            "description": "Inspect data",
            "parameters": {
                "type": "object",
                "properties": {"input": {"type": "string", "description": "schema " * 1000}},
            },
        }] if section == "tools" else [],
    )
    middleware = ContextLimitMiddleware(100, "", [])

    def handler(_request):
        pytest.fail("An oversized request must not reach the model")

    with pytest.raises(ValueError, match="exceeds configured max_context_tokens"):
        if use_async:
            asyncio.run(middleware.awrap_model_call(request, handler))
        else:
            middleware.wrap_model_call(request, handler)


@pytest.mark.parametrize("use_async", [False, True])
@pytest.mark.parametrize("history_chars", [20_000, 8_000])
def test_desktop_context_limit_checks_compacted_model_request(tmp_path, monkeypatch, use_async, history_chars):
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from pydantic import Field

    from vertical_harness.engine import compose

    class TrackingModel(FakeMessagesListChatModel):
        seen_messages: list = Field(default_factory=list)

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            self.seen_messages.append(messages)
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    model = TrackingModel(
        responses=[
            AIMessage(content="History summary."),
            AIMessage(content="Done."),
            AIMessage(content="Follow-up done."),
        ],
        profile={"max_input_tokens": 20_000},
    )
    monkeypatch.setattr(compose, "init_configured_model", lambda _settings: model)
    selected = {**options(), "permission_mode": "full", "max_context_tokens": 12_000}
    selected["agent_content"] = ""
    selected["skills"] = []
    settings = HarnessSettings(
        _env_file=None,
        model="test:context-limit",
        workspace_root=tmp_path / "workspace",
        runtime_data_dir=tmp_path / "runtime",
        store_backend="memory",
        enable_memory=False,
    )
    history = [
        message
        for index in range(6)
        for message in (
            HumanMessage(content=f"Historical request {index}: " + "x" * history_chars),
            AIMessage(content=f"Verified result {index} was recorded."),
        )
    ]
    config = {"configurable": {"thread_id": f"context-limit-{use_async}"}}

    async def scenario():
        async with aopen_desktop_agent(settings, selected) as app:
            if use_async:
                result = await app.ainvoke({"messages": history}, config)
                snapshot = await app.aget_state(config)
            else:
                result = app.invoke({"messages": history}, config)
                snapshot = app.get_state(config)
            assert result["messages"][-1].content == "Done."
            assert any("Historical request 0" in str(message) for message in snapshot.values["messages"])
            assert "Historical request 0" not in repr(model.seen_messages[-1])
            follow_up = {"messages": [HumanMessage(content="Continue from the verified result.")]}
            if use_async:
                continued = await app.ainvoke(follow_up, config)
            else:
                continued = app.invoke(follow_up, config)
            assert continued["messages"][-1].content == "Follow-up done."
            assert len(model.seen_messages) == 3
            assert "Historical request 0" not in repr(model.seen_messages[-1])

    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["read_only", "ask", "full"])
def test_build_agent_composes_each_desktop_permission_mode(tmp_path, mode):
    selected = options()
    selected["permission_mode"] = mode
    settings = HarnessSettings.model_validate(
        {
            "model": "deepseek:deepseek-chat",
            "workspace_root": tmp_path,
            "runtime_data_dir": tmp_path,
            "enabled_plugins": [],
        }
    )
    agent = build_agent(
        settings,
        store=InMemoryStore(),
        checkpointer=InMemorySaver(),
        mcp_tools=[],
        plugins=[DesktopPlugin(selected)],
    )
    assert agent.get_graph().nodes


def test_read_only_real_graph_blocks_builtin_write_file(tmp_path, monkeypatch):
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

    from vertical_harness.engine import compose

    class ToolCallingFakeModel(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    monkeypatch.setattr(
        compose,
        "init_configured_model",
        lambda _settings: ToolCallingFakeModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "write_file",
                            "args": {"file_path": "blocked.txt", "content": "x"},
                            "id": "write-call",
                            "type": "tool_call",
                        }
                    ],
                )
            ]
        ),
    )
    selected = options()
    selected["permission_mode"] = "read_only"
    settings = HarnessSettings.model_validate(
        {
            "model": "deepseek:deepseek-chat",
            "workspace_root": tmp_path / "workspace",
            "runtime_data_dir": tmp_path / "runtime",
            "enabled_plugins": [],
        }
    )

    async def invoke():
        async with aopen_desktop_agent(settings, selected) as app:
            assert app.config["recursion_limit"] == 9_999
            await app.ainvoke(
                {"messages": [HumanMessage(content="write blocked.txt")]},
                {"configurable": {"thread_id": "readonly-check"}},
            )

    with pytest.raises(PermissionError, match="disabled in read_only"):
        asyncio.run(invoke())
    assert not (tmp_path / "blocked.txt").exists()


def test_ask_rejection_returns_tool_error_and_allows_model_to_continue(tmp_path, monkeypatch):
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

    from vertical_harness.engine import compose

    class ToolCallingFakeModel(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    monkeypatch.setattr(
        compose,
        "init_configured_model",
        lambda _settings: ToolCallingFakeModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "write_file",
                            "args": {"file_path": "rejected.txt", "content": "x"},
                            "id": "write-call",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="I will leave that file unchanged."),
            ]
        ),
    )
    selected = options()
    selected["permission_mode"] = "ask"
    settings = HarnessSettings.model_validate(
        {
            "model": "deepseek:deepseek-chat",
            "workspace_root": tmp_path / "workspace",
            "runtime_data_dir": tmp_path / "runtime",
            "enabled_plugins": [],
        }
    )

    async def scenario():
        async with aopen_desktop_agent(settings, selected) as app:
            config = {"configurable": {"thread_id": "ask-reject-check"}}
            await app.ainvoke({"messages": [HumanMessage(content="write rejected.txt")]}, config)
            paused = await app.aget_state(config)
            pending = paused.tasks[0].interrupts[0]
            result = await app.ainvoke(
                Command(resume={pending.id: {"decisions": [{"type": "reject"}]}}),
                config,
            )
            assert result["messages"][-2].status == "error"
            assert result["messages"][-2].tool_call_id == "write-call"
            assert result["messages"][-1].content == "I will leave that file unchanged."

    asyncio.run(scenario())
    assert not (tmp_path / "workspace" / "rejected.txt").exists()


@pytest.mark.parametrize("permission_mode", ["ask", "full"])
def test_composed_plugin_dispatch_requires_one_time_authorization(tmp_path, monkeypatch, permission_mode):
    import json

    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

    from vertical_harness import PluginAuthorizationGateway
    from vertical_harness.engine import compose
    from vertical_plugins.common import BaseWorkflow, WorkflowPlan, WorkflowPlugin
    from vertical_plugins.common.contracts import PluginManifest, SideEffect

    executions = []
    plugin_builds = []

    class AuthWorkflow(BaseWorkflow):
        plugin_id = "demo.auth"
        workflow_name = "auth_workflow"
        description = "Test workflow requiring authorization"

        def run(self) -> WorkflowPlan:
            return self.pipeline(self.step("run", self.execute))

        def execute(self, state):
            executions.append(state)
            return {"messages": [AIMessage(content="workflow executed")]}

    class AuthPlugin(WorkflowPlugin):
        workflow_cls = AuthWorkflow

        def build(self, services):
            plugin_builds.append(self)
            return super().build(services)

        @property
        def manifest(self):
            return PluginManifest(plugin_id="demo.auth", version="1.0.0", description="Auth test")

    class TaskModel(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    call = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "task",
                "args": {
                    "subagent_type": "auth_workflow",
                    "description": json.dumps({"purpose": "check"}),
                },
                "id": "auth-workflow-call",
                "type": "tool_call",
            }
        ],
    )
    repeated_call = call.model_copy(
        update={"tool_calls": [{**call.tool_calls[0], "id": "auth-workflow-call-2"}]}
    )
    monkeypatch.setattr(
        compose,
        "init_configured_model",
        lambda _settings: TaskModel(
            responses=[call, repeated_call, AIMessage(content="workflow finished")]
        ),
    )
    plugin = AuthPlugin()
    monkeypatch.setattr("vertical_harness.desktop_runtime.discover_plugins", lambda _ids: [plugin])
    plugin_options = options()
    plugin_options["permission_mode"] = permission_mode
    plugin_options["enabled_plugins"] = ["demo.auth"]
    desktop_instructions = DesktopPlugin(
        plugin_options, selected_plugin_ids=frozenset({"demo.auth"})
    ).build(BuildServices(workspace_root=tmp_path)).instructions
    assert "Plugins selected for this user turn: demo.auth." in desktop_instructions
    assert plugin_builds == []
    settings = HarnessSettings.model_validate(
        {
            "model": "test:desktop-auth",
            "workspace_root": tmp_path / "workspace",
            "runtime_data_dir": tmp_path / "runtime",
            "enabled_plugins": ["demo.auth"],
            "supervisor_only": True,
        }
    )
    @asynccontextmanager
    async def scenario(selected_plugins, selected_skills=()):
        gateway = PluginAuthorizationGateway(
            installed_plugins=["demo.auth"], enabled_plugins=["demo.auth"],
            remember_authorizations=True,
        )
        scenario_name = (
            "selected" if selected_plugins else "skill" if selected_skills else "inferred"
        )
        thread_id = f"desktop-auth-thread-{scenario_name}"
        run_id = f"desktop-auth-run-{scenario_name}"
        gateway.select_for_run(thread_id, run_id, selected_plugins)
        async with aopen_desktop_agent(
            settings,
            plugin_options,
            auth_gateway=gateway,
            selected_skill_ids=frozenset(selected_skills),
            selected_plugin_ids=frozenset(selected_plugins),
        ) as app:
            config = {
                "configurable": {"thread_id": thread_id},
                "metadata": {
                    "desktop_run_id": run_id,
                    "desktop_selected_plugins": json.dumps(selected_plugins),
                    "desktop_selected_skills": json.dumps(list(selected_skills)),
                    "desktop_tenant_id": "tenant-a",
                    "desktop_user_id": "user-a",
                },
            }
            identity = RunContext(tenant_id="tenant-a", user_id="user-a")
            paused = await app.ainvoke(
                {"messages": [HumanMessage(content="run the workflow")]},
                config,
                context=identity,
            )
            yield app, config, identity, paused, gateway

    async def run_selected():
        async with scenario(["demo.auth"]) as (app, config, identity, _paused, _gateway):
            state = await app.aget_state(config)
            pending = state.tasks[0].interrupts[0]
            assert pending.value["kind"] == "plugin_authorization"
            assert executions == []
            await app.ainvoke(Command(resume={pending.id: {
                "request_id": pending.value["request_id"], "approved": True,
            }}), config, context=identity)
            assert len(executions) == 2

    asyncio.run(run_selected())
    executions.clear()

    async def run_inferred_then_approve():
        async with scenario([]) as (app, config, identity, _paused, gateway):
            state = await app.aget_state(config)
            assert state.tasks and state.tasks[0].interrupts, state.values.get("messages")
            pending = state.tasks[0].interrupts[0]
            request = pending.value
            assert request["kind"] == "plugin_authorization"
            assert request["plugin_id"] == "demo.auth"
            assert request["request_id"]
            assert executions == []
            resumed = await app.ainvoke(
                Command(
                    resume={
                        pending.id: {
                            "request_id": request["request_id"],
                            "approved": True,
                        }
                    }
                ),
                config,
                context=identity,
            )
            assert "workflow executed" in str(resumed["messages"])
            assert len(executions) == 2
            gateway.end_run(config["configurable"]["thread_id"], "desktop-auth-run-inferred")
            gateway.verify_execution(
                plugin_id="demo.auth",
                workflow_name="auth_workflow",
                user_id="user-a",
                tenant_id="tenant-a",
                thread_id="another-conversation",
                run_id="another-run",
                input_data={},
                effect=SideEffect.READ,
            )

    asyncio.run(run_inferred_then_approve())
    executions.clear()

    async def skill_alone_does_not_authorize():
        async with scenario([], ["demo.auth:skill"]) as (app, config, _identity, _paused, _gateway):
            state = await app.aget_state(config)
            assert state.tasks[0].interrupts[0].value["kind"] == "plugin_authorization"
            assert executions == []

    asyncio.run(skill_alone_does_not_authorize())
    assert len(plugin_builds) == 3


def test_streaming_six_tool_calls_keeps_agent_recursion_limit(tmp_path, monkeypatch):
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel

    from vertical_harness.engine import compose

    class ToolCallingFakeModel(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    responses = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "ls",
                    "args": {"path": "."},
                    "id": f"ls-call-{index}",
                    "type": "tool_call",
                }
            ],
        )
        for index in range(6)
    ]
    responses.append(AIMessage(content="Finished after six listings."))
    monkeypatch.setattr(
        compose,
        "init_configured_model",
        lambda _settings: ToolCallingFakeModel(responses=responses),
    )
    selected = options()
    selected["permission_mode"] = "full"
    settings = HarnessSettings.model_validate(
        {
            "model": "deepseek:deepseek-chat",
            "workspace_root": tmp_path / "workspace",
            "runtime_data_dir": tmp_path / "runtime",
            "enabled_plugins": [],
        }
    )

    async def scenario():
        async with aopen_desktop_agent(settings, selected) as app:
            assert app.config["recursion_limit"] == 9_999
            config = {
                **make_checkpoint_config("six-tool-call-check"),
                "run_id": "six-tool-run",
                "recursion_limit": app.config["recursion_limit"],
            }
            events = [
                event
                async for event in astream_agent(
                    app,
                    {
                        "messages": [
                            HumanMessage(content="List this directory six times, then stop.")
                        ]
                    },
                    config=config,
                    context=RunContext(tenant_id="test", user_id="test"),
                )
            ]
            assert sum(event.type == "tool.completed" for event in events) == 6
            assert any(event.type == "run.completed" for event in events)
            assert not any(event.type == "run.failed" for event in events)

    asyncio.run(scenario())


def test_clearing_durable_interrupt_allows_new_message_with_pending_tool_call():
    async def scenario():
        graph = StateGraph(MessagesState)

        def plan(state):
            if state["messages"][-1].content == "first task":
                return {
                    "messages": [
                        AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "name": "write_file",
                                    "args": {"file_path": "note.txt", "content": "x"},
                                    "id": "call-1",
                                    "type": "tool_call",
                                }
                            ],
                        )
                    ]
                }
            return {"messages": [AIMessage(content="second task completed")]}

        def action(_state):
            interrupt({"action_requests": [{"name": "write_file"}], "kind": "approval"})
            return {"messages": [ToolMessage(content="written", tool_call_id="call-1")]}

        graph.add_node("plan", plan)
        graph.add_node("action", action)
        graph.add_edge(START, "plan")
        graph.add_conditional_edges(
            "plan", lambda state: "action" if state["messages"][-1].tool_calls else END
        )
        graph.add_edge("action", END)
        app = graph.compile(checkpointer=InMemorySaver())
        config = {"configurable": {"thread_id": "stop-resume-check"}}

        await app.ainvoke({"messages": [HumanMessage(content="first task")]}, config)
        interrupted = await app.aget_state(config)
        assert interrupted.tasks[0].interrupts
        assert interrupted.values["messages"][-1].tool_calls

        await app.aupdate_state(config, interrupted.values, as_node=interrupted.next[0])
        resumed = await app.ainvoke({"messages": [HumanMessage(content="second task")]}, config)
        assert resumed["messages"][-1].content == "second task completed"

    asyncio.run(scenario())
