"""Exercise the real task boundary, not only direct workflow invocation."""

import json
from pathlib import Path

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from vertical_harness import HarnessSettings, RunContext, build_agent
from vertical_harness.engine import compose
from vertical_harness.engine.policy import PluginAuthorizationGateway
from vertical_plugins.data_analysis import DataAnalysisPlugin


class TaskModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


@pytest.mark.parametrize("async_mode", [False, True])
def test_sequential_analysis_tasks_validate_and_do_not_reuse_prior_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, async_mode: bool
) -> None:
    import asyncio

    (tmp_path / "sample.csv").write_text(
        "price,views,group\n1,2,A\n2,4,A\n3,6,B\n未显示,8,B\n", encoding="utf-8"
    )
    payloads = [
        {"task_type": "profile", "file_path": "sample.csv"},
        {"file_path": "sample.csv", "analysis_goal": "describe prices"},
        {
            "file_path": "sample.csv",
            "analysis_type": "correlation",
            "target_columns": ["price", "views"],
        },
        {"file_path": "sample.csv", "analysis_type": "correlation"},
        {
            "file_path": "sample.csv",
            "analysis_type": "group_comparison",
            "target_columns": ["price"],
            "group_column": "group",
        },
    ]
    responses = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "task",
                    "args": {
                        "subagent_type": "data_analysis_workflow",
                        "description": json.dumps(payload),
                    },
                    "id": f"analysis-{i}",
                }
            ],
        )
        for i, payload in enumerate(payloads)
    ] + [AIMessage(content="finished")]
    monkeypatch.setattr(compose, "init_configured_model", lambda _: TaskModel(responses=responses))
    gateway = PluginAuthorizationGateway(
        enabled_plugins=["data_analysis"], remember_authorizations=True
    )
    settings = HarnessSettings(
        model="test:analysis",
        workspace_root=tmp_path,
        enabled_plugins=["data_analysis"],
        store_backend="memory",
    )
    agent = build_agent(
        settings,
        plugins=[DataAnalysisPlugin()],
        checkpointer=InMemorySaver(),
        auth_gateway=gateway,
        enforce_plugin_authorization=True,
    )
    config = {"configurable": {"thread_id": "analysis-thread"}, "recursion_limit": 100}
    inputs = {"messages": [HumanMessage(content="test the data analysis plugin")]}
    context = RunContext(tenant_id="default", user_id="default")
    if async_mode:
        result = asyncio.run(agent.ainvoke(inputs, config, context=context))
    else:
        result = agent.invoke(inputs, config, context=context)
    pending = result["__interrupt__"]
    assert len(pending) == 1
    assert pending[0].value["kind"] == "plugin_authorization"
    resume = Command(resume={"request_id": pending[0].value["request_id"], "approved": True})
    if async_mode:
        result = asyncio.run(agent.ainvoke(resume, config, context=context))
    else:
        result = agent.invoke(resume, config, context=context)
    assert not result.get("__interrupt__"), result
    tools = [
        m
        for m in result["messages"]
        if isinstance(m, ToolMessage) and m.tool_call_id.startswith("analysis-")
    ]
    assert len(tools) == 5, result
    assert all(m.status == "success" for m in tools), [m.content for m in tools]
    profile, descriptive, correlation, invalid, grouped = [json.loads(m.content) for m in tools]
    assert profile["profile"]["row_count"] == 4
    assert "report_path" not in profile
    assert descriptive["analysis"]["analysis_type"] == "descriptive"
    assert correlation["analysis"]["analysis_type"] == "correlation"
    assert correlation["analysis"]["correlations"][0]["n"] == 3
    assert correlation["analysis"]["correlations"][0]["pearson_r"] == 1.0
    assert descriptive["evidence_sha256"] != correlation["evidence_sha256"]
    assert invalid["status"] == "invalid_input"
    assert "target_columns" in invalid["error"]
    assert "report_path" not in invalid
    assert "analysis" not in invalid
    assert grouped["status"] == "passed"
    assert grouped["analysis"]["analysis_type"] == "group_comparison"
    assert grouped["analysis"]["group_comparisons"][0]["groups"]["A"]["n"] == 2
