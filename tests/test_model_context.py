"""Exercise request preparation through the assembled Deep Agents graph."""

import asyncio

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.messages.utils import count_tokens_approximately
from pydantic import Field

from vertical_harness.config import HarnessSettings
from vertical_harness.core import compose
from vertical_harness.core.context import RunContext


class ContextModel(FakeMessagesListChatModel):
    seen: list = Field(default_factory=list)
    bindings: list = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        self.bindings.append(list(tools))
        return self

    def _generate(self, messages, *args, **kwargs):
        self.seen.append(list(messages))
        return super()._generate(messages, *args, **kwargs)


def history():
    return [
        message
        for index in range(6)
        for message in (
            HumanMessage(content=f"Historical request {index}: " + "x" * 4000),
            AIMessage(content=f"Verified result {index}"),
        )
    ] + [HumanMessage(content="Continue the unfinished work.")]


@pytest.mark.parametrize("use_async", [False, True])
def test_project_instructions_trigger_compaction_before_budget_rejection(
    tmp_path, monkeypatch, use_async
):
    (tmp_path / "AGENTS.md").write_text("PROJECT_RULE " * 1200, encoding="utf-8")
    model = ContextModel(
        responses=[AIMessage(content="History summary."), AIMessage(content="Done.")],
        profile={"max_input_tokens": 100_000},
    )
    monkeypatch.setattr(compose.ModelRouting, "init_configured_model", lambda _: model)
    agent = compose.AgentComposer.build_agent(
        HarnessSettings(
            _env_file=None, model="test:model", workspace_root=tmp_path,
            store_backend="memory", enable_planning=False,
        ),
        options={"max_context_tokens": 13_000},
    )
    config = {"configurable": {"thread_id": "instructions-budget"}}
    request = {"messages": history()}
    result = asyncio.run(agent.ainvoke(request, config)) if use_async else agent.invoke(request, config)
    assert result["messages"][-1].content == "Done."
    assert len(model.seen) == 2
    assert "Historical request 0" not in repr(model.seen[-1])
    assert "PROJECT_RULE" in repr(model.seen[-1][0])
    assert "Historical request 0" in repr(agent.get_state(config).values["messages"])


@pytest.mark.parametrize("use_async", [False, True])
def test_routed_model_capacity_is_checked_before_sending_history(tmp_path, monkeypatch, use_async):
    summarizer = ContextModel(
        responses=[AIMessage(content="History summary.")],
        profile={"max_input_tokens": 20_000},
    )
    economy = ContextModel(
        responses=[AIMessage(content="Economy done.")],
        profile={"max_input_tokens": 10_000},
    )
    monkeypatch.setattr(
        compose.ModelRouting, "init_configured_model",
        lambda _, model=None: economy if model == "test:economy" else summarizer,
    )
    agent = compose.AgentComposer.build_agent(HarnessSettings(
        _env_file=None, model="test:quality", economy_model="test:economy",
        quality_model="test:quality", workspace_root=tmp_path,
        store_backend="memory", enable_planning=False,
    ))
    config = {"configurable": {"thread_id": "routed-budget"}}
    request = {"messages": history()}
    context = RunContext("tenant", "user", "economy")
    result = (
        asyncio.run(agent.ainvoke(request, config, context=context))
        if use_async else agent.invoke(request, config, context=context)
    )
    assert result["messages"][-1].content == "Economy done."
    assert len(economy.seen) == 1
    assert count_tokens_approximately(economy.seen[0], tools=economy.bindings[0]) <= 9_500
    assert "Historical request 0" not in repr(economy.seen[0])
    assert "Historical request 0" in repr(agent.get_state(config).values["messages"])


@pytest.mark.parametrize("use_async", [False, True])
def test_read_only_model_only_sees_allowed_tools(tmp_path, monkeypatch, use_async):
    model = ContextModel(responses=[AIMessage(content="Read only.")])
    monkeypatch.setattr(compose.ModelRouting, "init_configured_model", lambda _: model)
    agent = compose.AgentComposer.build_agent(
        HarnessSettings(_env_file=None, model="test:model", workspace_root=tmp_path),
        options={"permission_mode": "read_only"},
    )
    request = {"messages": [HumanMessage(content="Inspect the project.")]}
    config = {"configurable": {"thread_id": "read-only-tools"}}
    if use_async:
        asyncio.run(agent.ainvoke(request, config))
    else:
        agent.invoke(request, config)
    exposed = {tool.name for tool in model.bindings[0]}
    assert {"read_file", "glob", "grep", "web_search"} <= exposed
    assert exposed <= {
        "read_file", "ls", "glob", "grep", "ask_user", "web_search", "write_todos",
    }


@pytest.mark.parametrize("use_async", [False, True])
def test_missing_instructions_do_not_shift_checkpoint_summary_cutoff(tmp_path, monkeypatch, use_async):
    model = ContextModel(responses=[AIMessage(content="Continued.")])
    monkeypatch.setattr(compose.ModelRouting, "init_configured_model", lambda _: model)
    agent = compose.AgentComposer.build_agent(HarnessSettings(
        _env_file=None, model="test:model", workspace_root=tmp_path,
        store_backend="memory", enable_planning=False,
    ))
    config = {"configurable": {"thread_id": "missing-instructions"}}
    agent.update_state(config, {
        "messages": [
            HumanMessage(content="Original task"),
            HumanMessage(content="STALE_INSTRUCTIONS", additional_kwargs={
                "harness_instructions": [str(tmp_path / "removed" / "AGENTS.md")],
            }),
            AIMessage(content="Earlier result"),
        ],
        "_summarization_event": {
            "cutoff_index": 3,
            "summary_message": HumanMessage(content="Earlier task summary"),
            "file_path": None,
        },
    })
    request = {"messages": [HumanMessage(content="NEW_USER_REQUEST")]}
    if use_async:
        asyncio.run(agent.ainvoke(request, config))
    else:
        agent.invoke(request, config)
    assert "NEW_USER_REQUEST" in repr(model.seen[0])
    assert "STALE_INSTRUCTIONS" not in repr(model.seen[0])
