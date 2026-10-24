"""通过真实 Deep Agents 图验证装配、生命周期与原生扩展边界。"""

import asyncio
from dataclasses import dataclass

import pytest
from deepagents.middleware.filesystem import FilesystemMiddleware
from deepagents.middleware.summarization import SummarizationMiddleware
from langchain.agents.middleware import AgentMiddleware, ModelFallbackMiddleware, hook_config
from langchain.agents.structured_output import ToolStrategy
from langchain.tools import ToolRuntime
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import ToolException, tool
from langgraph.store.memory import InMemoryStore
from langgraph.types import Command
from pydantic import Field

from vertical_harness.core import compose
from vertical_harness.core.context import RunContext
from vertical_harness.langchain.streaming import AgentStreaming
from vertical_harness.models import HarnessSettings
from vertical_harness.runtime.agent_runtime import AgentRuntime


class Model(FakeMessagesListChatModel):
    seen: list = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, *args, **kwargs):
        self.seen.append(list(messages))
        return super()._generate(messages, *args, **kwargs)


class Lifecycle(AgentMiddleware):
    def __init__(self, label, records, *, stop=False):
        self.label, self.records, self.stop = label, records, stop

    @property
    def name(self):
        return self.label

    def before_agent(self, state, runtime):
        self.records.append(f"{self.label}.before_agent")

    @hook_config(can_jump_to=["end"])
    def before_model(self, state, runtime):
        self.records.append(f"{self.label}.before_model")
        if self.stop:
            return {"messages": [AIMessage(content="stopped")], "jump_to": "end"}

    def wrap_model_call(self, request, handler):
        self.records.append(f"{self.label}.model.enter")
        result = handler(request)
        self.records.append(f"{self.label}.model.exit")
        return result

    async def awrap_model_call(self, request, handler):
        self.records.append(f"{self.label}.model.enter")
        result = await handler(request)
        self.records.append(f"{self.label}.model.exit")
        return result

    def after_model(self, state, runtime):
        self.records.append(f"{self.label}.after_model")

    def wrap_tool_call(self, request, handler):
        self.records.append(f"{self.label}.tool.enter")
        result = handler(request)
        self.records.append(f"{self.label}.tool.exit")
        return result

    async def awrap_tool_call(self, request, handler):
        self.records.append(f"{self.label}.tool.enter")
        result = await handler(request)
        self.records.append(f"{self.label}.tool.exit")
        return result

    def after_agent(self, state, runtime):
        self.records.append(f"{self.label}.after_agent")


@dataclass
class Answer:
    total: int


def settings(tmp_path, **kwargs):
    return HarnessSettings(
        _env_file=None,
        model="test:model",
        workspace_root=tmp_path,
        store_backend="memory",
        enable_planning=False,
        **kwargs,
    )


def build(tmp_path, monkeypatch, model, **kwargs):
    monkeypatch.setattr(compose.ModelRouting, "init_configured_model", lambda _: model)
    return compose.AgentComposer.build_agent(settings(tmp_path), **kwargs)


def call(name, args=None, identity="call"):
    return {"id": identity, "name": name, "args": args or {}}


@pytest.mark.parametrize("asynchronous", [False, True])
def test_native_lifecycle_order_and_tool_return_to_model(tmp_path, monkeypatch, asynchronous):
    records = []

    @tool
    def echo() -> str:
        """Return evidence to the model."""
        records.append("tool.executed")
        return "evidence"

    model = Model(responses=[AIMessage(content="", tool_calls=[call("echo")]), AIMessage("done")])
    agent = build(
        tmp_path,
        monkeypatch,
        model,
        tools=[echo],
        middleware=[Lifecycle("a", records), Lifecycle("b", records)],
    )
    inputs, config = (
        {"messages": [HumanMessage("verify")]},
        {"configurable": {"thread_id": "hooks"}},
    )
    result = (
        asyncio.run(agent.ainvoke(inputs, config)) if asynchronous else agent.invoke(inputs, config)
    )
    round_trip = [
        "a.before_model",
        "b.before_model",
        "a.model.enter",
        "b.model.enter",
        "b.model.exit",
        "a.model.exit",
        "b.after_model",
        "a.after_model",
    ]
    assert records == [
        "a.before_agent",
        "b.before_agent",
        *round_trip,
        "a.tool.enter",
        "b.tool.enter",
        "tool.executed",
        "b.tool.exit",
        "a.tool.exit",
        *round_trip,
        "b.after_agent",
        "a.after_agent",
    ]
    assert any(isinstance(m, ToolMessage) and m.content == "evidence" for m in model.seen[-1])
    assert result["messages"][-1].content == "done"


def test_jump_to_end_runs_after_agent_without_model_call(tmp_path, monkeypatch):
    records = []
    model = Model(responses=[AIMessage("must not run")])
    agent = build(tmp_path, monkeypatch, model, middleware=[Lifecycle("guard", records, stop=True)])
    result = agent.invoke(
        {"messages": [HumanMessage("stop")]}, {"configurable": {"thread_id": "end"}}
    )
    assert result["messages"][-1].content == "stopped"
    assert records == ["guard.before_agent", "guard.before_model", "guard.after_agent"]
    assert model.seen == []


@pytest.mark.parametrize("failure", ["error", "cancel", "timeout"])
def test_after_agent_is_not_finally_and_tools_are_cleaned_up(tmp_path, monkeypatch, failure):
    entered, cleaned = asyncio.Event(), asyncio.Event()
    records = []

    @tool
    async def wait() -> str:
        """Wait until cancelled or fail."""
        try:
            entered.set()
            if failure == "error":
                raise RuntimeError("private failure")
            await asyncio.Event().wait()
            return "unreachable"
        finally:
            cleaned.set()

    agent = build(
        tmp_path,
        monkeypatch,
        Model(responses=[AIMessage(content="", tool_calls=[call("wait")])]),
        tools=[wait],
        middleware=[Lifecycle("audit", records)],
    )

    async def run():
        task = asyncio.create_task(
            AgentStreaming.ainvoke_agent(
                agent,
                {"messages": [HumanMessage("run")]},
                config={"configurable": {"thread_id": "cleanup"}},
                timeout_seconds=0.1 if failure == "timeout" else 5,
            )
        )
        await asyncio.wait_for(entered.wait(), 2)
        if failure == "cancel":
            task.cancel()
        with pytest.raises(
            {"error": RuntimeError, "cancel": asyncio.CancelledError, "timeout": TimeoutError}[
                failure
            ]
        ):
            await task
        assert cleaned.is_set()

    asyncio.run(run())
    assert "audit.after_agent" not in records


def test_parallel_tools_preserve_partial_success_and_reducer_updates(tmp_path, monkeypatch):
    entered = set()
    ready = asyncio.Event()

    @tool
    async def parallel(value: str, runtime: ToolRuntime) -> Command:
        """Run two calls concurrently, one returning an expected tool error."""
        entered.add(value)
        if len(entered) == 2:
            ready.set()
        await asyncio.wait_for(ready.wait(), 2)
        if value == "bad":
            raise ToolException("service unavailable")
        return Command(
            update={"messages": [ToolMessage(content="saved", tool_call_id=runtime.tool_call_id)]}
        )

    parallel.handle_tool_error = True
    model = Model(
        responses=[
            AIMessage(
                content="", tool_calls=[call("parallel", {"value": v}, v) for v in ["ok", "bad"]]
            ),
            AIMessage("partial success reported"),
        ]
    )
    agent = build(tmp_path, monkeypatch, model, tools=[parallel])
    result = asyncio.run(
        agent.ainvoke(
            {"messages": [HumanMessage("parallel")]},
            {"configurable": {"thread_id": "parallel"}},
        )
    )
    messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert {m.tool_call_id: m.status for m in messages} == {"ok": "success", "bad": "error"}
    assert {m.tool_call_id for m in model.seen[-1] if isinstance(m, ToolMessage)} == {"ok", "bad"}


@pytest.mark.parametrize("asynchronous", [False, True])
def test_store_is_available_to_tool_and_is_borrowed(tmp_path, monkeypatch, asynchronous):
    store = InMemoryStore()

    @tool
    def remember(runtime: ToolRuntime[RunContext]) -> str:
        """Persist a fact using trusted identity rather than tool arguments."""
        context = runtime.context
        namespace = (context.tenant_id, context.user_id, "memories")
        runtime.store.put(namespace, "fact", {"value": 42})
        return "saved"

    model = Model(
        responses=[AIMessage(content="", tool_calls=[call("remember")]), AIMessage("done")]
    )
    agent = build(tmp_path, monkeypatch, model, tools=[remember], store=store)
    inputs, config = (
        {"messages": [HumanMessage("remember")]},
        {"configurable": {"thread_id": "store"}},
    )
    context = RunContext("tenant", "user")
    if asynchronous:
        asyncio.run(agent.ainvoke(inputs, config, context=context))
    else:
        agent.invoke(inputs, config, context=context)
    assert agent.store is store
    assert store.get(("tenant", "user", "memories"), "fact").value == {"value": 42}
    assert store.get(("tenant", "other", "memories"), "fact") is None


def test_runtime_and_composer_contexts_inject_the_owned_store(tmp_path, monkeypatch):
    model = Model(responses=[AIMessage("done")])
    monkeypatch.setattr(compose.ModelRouting, "init_configured_model", lambda _: model)
    configured = settings(tmp_path)
    with compose.AgentComposer.open_agent(configured) as graph:
        assert graph.store is not None

    async def run():
        async with compose.AgentComposer.aopen_agent(configured) as graph:
            assert graph.store is not None
        runtime = await AgentRuntime.create(configured, RunContext("tenant", "user"))
        try:
            async with runtime.open_agent() as graph:
                assert graph.store is runtime.store
            await runtime.store.aput(("test",), "alive", {"value": True})
            assert (await runtime.store.aget(("test",), "alive")).value == {"value": True}
        finally:
            await runtime.aclose()

    asyncio.run(run())


def test_main_and_all_declarative_children_have_independent_policy_instances(tmp_path, monkeypatch):
    original = compose.create_deep_agent
    captured = {}

    def capture(**kwargs):
        captured.update(kwargs)
        return original(**kwargs)

    monkeypatch.setattr(compose, "create_deep_agent", capture)
    build(
        tmp_path,
        monkeypatch,
        Model(responses=[AIMessage("done")]),
        subagents=[
            {
                "name": "research",
                "description": "Scoped research",
                "system_prompt": "Report evidence",
            }
        ],
    )
    stacks = [captured["middleware"], *(s["middleware"] for s in captured["subagents"])]
    assert len(stacks) == 3
    assert all([m.name for m in stack] == [m.name for m in stacks[0]] for stack in stacks)
    assert len({id(m) for stack in stacks for m in stack}) == sum(map(len, stacks))
    for stack in stacks:
        summary = next(m for m in stack if m.name == "SummarizationMiddleware")
        assert len(summary._request_middleware) == 2


def test_native_structured_output_uses_composition_extension(tmp_path, monkeypatch):
    model = Model(responses=[AIMessage(content="", tool_calls=[call("Answer", {"total": 42})])])
    agent = build(tmp_path, monkeypatch, model, response_format=ToolStrategy(Answer))
    result = agent.invoke(
        {"messages": [HumanMessage("total")]}, {"configurable": {"thread_id": "schema"}}
    )
    assert result["structured_response"] == Answer(total=42)


def test_official_fallback_can_be_injected_without_another_agent_loop(tmp_path, monkeypatch):
    class FailingModel(Model):
        def _generate(self, *args, **kwargs):
            raise RuntimeError("provider unavailable")

    fallback = Model(responses=[AIMessage("fallback answer")])
    agent = build(
        tmp_path,
        monkeypatch,
        FailingModel(responses=[]),
        middleware=[ModelFallbackMiddleware(fallback)],
    )
    result = agent.invoke(
        {"messages": [HumanMessage("answer")]}, {"configurable": {"thread_id": "fallback"}}
    )
    assert result["messages"][-1].content == "fallback answer"


def test_fallback_model_is_checked_against_its_own_context_window(tmp_path, monkeypatch):
    from langchain_core.exceptions import ContextOverflowError

    class FailingModel(Model):
        def _generate(self, *args, **kwargs):
            raise RuntimeError("provider unavailable")

    fallback = Model(responses=[AIMessage("must not run")], profile={"max_input_tokens": 1})
    agent = build(
        tmp_path,
        monkeypatch,
        FailingModel(responses=[]),
        middleware=[ModelFallbackMiddleware(fallback)],
    )
    with pytest.raises(ContextOverflowError):
        agent.invoke(
            {"messages": [HumanMessage("answer")]},
            {"configurable": {"thread_id": "fallback-limit"}},
        )
    assert fallback.seen == []


def test_custom_subagent_is_dispatched_by_native_task_tool(tmp_path, monkeypatch):
    model = Model(
        responses=[
            AIMessage(
                content="",
                tool_calls=[call("task", {"subagent_type": "research", "description": "inspect"})],
            ),
            AIMessage("child evidence"),
            AIMessage("done"),
        ]
    )
    agent = build(
        tmp_path,
        monkeypatch,
        model,
        subagents=[
            {
                "name": "research",
                "description": "Inspect evidence",
                "system_prompt": "Research marker",
            }
        ],
    )
    result = agent.invoke(
        {"messages": [HumanMessage("delegate")]}, {"configurable": {"thread_id": "child"}}
    )
    assert result["messages"][-1].content == "done"
    assert "Research marker" in model.seen[1][0].text
    assert "child evidence" in next(
        m.content for m in result["messages"] if isinstance(m, ToolMessage)
    )


def test_extension_tool_uses_native_approval_without_core_name_changes(tmp_path, monkeypatch):
    executed = []

    @tool
    def publish() -> str:
        """Publish an external business change."""
        executed.append(True)
        return "published"

    publish.metadata = {"requires_approval": True}
    model = Model(
        responses=[AIMessage(content="", tool_calls=[call("publish")]), AIMessage("done")]
    )
    monkeypatch.setattr(compose.ModelRouting, "init_configured_model", lambda _: model)
    agent = compose.AgentComposer.build_agent(
        settings(tmp_path, permission_mode="ask"), tools=[publish]
    )
    config = {"configurable": {"thread_id": "extension-approval"}}
    pending = agent.invoke({"messages": [HumanMessage("publish")]}, config)
    assert (
        pending["__interrupt__"][0].value.items()
        >= {
            "type": "tool_approval",
            "tool_call_id": "call",
            "name": "publish",
            "args": {},
        }.items()
    )
    assert executed == []
    agent.invoke(Command(resume={"type": "approve"}), config)
    assert executed == [True]


def test_final_native_stacks_have_one_filesystem_and_distinct_hook_names(tmp_path, monkeypatch):
    import deepagents.graph as native
    import deepagents.middleware.subagents as children

    main_create, child_create = native.create_agent, children.create_agent
    stacks = {}

    def capture_main(*args, **kwargs):
        stacks[kwargs["name"]] = kwargs["middleware"]
        return main_create(*args, **kwargs)

    def capture_child(*args, **kwargs):
        stacks[kwargs["name"]] = kwargs["middleware"]
        return child_create(*args, **kwargs)

    monkeypatch.setattr(native, "create_agent", capture_main)
    monkeypatch.setattr(children, "create_agent", capture_child)
    graph = build(tmp_path, monkeypatch, Model(responses=[AIMessage("done")]))
    assert set(stacks) == {"vertical-agent", "general-purpose"}
    for stack in stacks.values():
        assert sum(isinstance(m, FilesystemMiddleware) for m in stack) == 1
        assert sum(isinstance(m, SummarizationMiddleware) for m in stack) == 1
        assert len({m.name for m in stack}) == len(stack)
        files = next(m for m in stack if isinstance(m, FilesystemMiddleware))
        assert "apply_patch" in {t.name for t in files.tools}
    assert "DirectoryInstructionsMiddleware.before_model" in graph.nodes


@pytest.mark.parametrize("asynchronous", [False, True])
def test_parallel_native_approvals_are_bound_to_individual_calls(
    tmp_path, monkeypatch, asynchronous
):
    executed = []

    @tool
    def publish(value: str) -> str:
        """Publish an approved value."""
        executed.append(value)
        return value

    publish.metadata = {"requires_approval": True}
    model = Model(
        responses=[
            AIMessage(
                content="",
                tool_calls=[call("publish", {"value": value}, value) for value in ("yes", "no")],
            ),
            AIMessage("done"),
        ]
    )
    monkeypatch.setattr(compose.ModelRouting, "init_configured_model", lambda _: model)
    graph = compose.AgentComposer.build_agent(
        settings(tmp_path, permission_mode="ask"), tools=[publish]
    )
    config = {"configurable": {"thread_id": "parallel-approvals"}}
    inputs = {"messages": [HumanMessage("publish")]}

    async def run():
        pending = await graph.ainvoke(inputs, config)
        assert executed == []
        decisions = {
            item.id: {"type": "approve" if item.value["tool_call_id"] == "yes" else "reject"}
            for item in pending["__interrupt__"]
        }
        assert len(decisions) == 2
        return await graph.ainvoke(Command(resume=decisions), config)

    if asynchronous:
        result = asyncio.run(run())
    else:
        pending = graph.invoke(inputs, config)
        assert executed == []
        decisions = {
            item.id: {"type": "approve" if item.value["tool_call_id"] == "yes" else "reject"}
            for item in pending["__interrupt__"]
        }
        assert len(decisions) == 2
        result = graph.invoke(Command(resume=decisions), config)
    assert executed == ["yes"]
    assert {m.tool_call_id for m in result["messages"] if isinstance(m, ToolMessage)} == {
        "yes",
        "no",
    }
    assert result["messages"][-1].content == "done"


def test_resume_does_not_repeat_a_completed_parallel_tool(tmp_path, monkeypatch):
    executed = []

    @tool
    def inspect_once() -> str:
        """Read once alongside a gated side effect."""
        executed.append("inspect")
        return "observed"

    @tool
    def publish() -> str:
        """Apply the authorized external change."""
        executed.append("publish")
        return "published"

    publish.metadata = {"requires_approval": True}
    model = Model(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    call("inspect_once", identity="inspect"),
                    call("publish", identity="publish"),
                ],
            ),
            AIMessage("done"),
        ]
    )
    monkeypatch.setattr(compose.ModelRouting, "init_configured_model", lambda _: model)
    agent = compose.AgentComposer.build_agent(
        settings(tmp_path, permission_mode="ask"), tools=[inspect_once, publish]
    )
    config = {"configurable": {"thread_id": "parallel-resume"}}

    async def run():
        pending = await agent.ainvoke({"messages": [HumanMessage("inspect then publish")]}, config)
        assert pending["__interrupt__"] and executed == ["inspect"]
        result = await agent.ainvoke(Command(resume={"type": "approve"}), config)
        assert result["messages"][-1].content == "done"
        assert executed == ["inspect", "publish"]

    asyncio.run(run())
