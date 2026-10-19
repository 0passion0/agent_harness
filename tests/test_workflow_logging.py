"""使用真实 Deep Agents 图和本地假模型验证完整执行树，无需 LLM 凭据。"""

from __future__ import annotations

import asyncio
import json
from asyncio import CancelledError
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from deepagents.backends.protocol import FileUploadResponse
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.errors import GraphInterrupt

from vertical_harness import (
    HarnessSettings,
    PluginContribution,
    PluginManifest,
    RunContext,
    WorkflowLoggingHandler,
    build_agent,
    configure_logging,
    log_context,
    open_store,
)
from vertical_harness.engine import compose
from vertical_plugins.research_citation import scan_numeric


class ToolCallingModel(FakeMessagesListChatModel):
    """保留框架消息处理与 callbacks，仅替换外部模型响应。"""

    def bind_tools(self, tools, **kwargs):
        return self


@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("delegate", [False, True])
def test_debug_covers_real_execution_and_subagents(
    async_mode, delegate, tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    config = HarnessSettings(
        _env_file=None,
        model="test:model",
        workspace_root=tmp_path,
        store_backend="memory",
        enable_planning=False,
        enable_skill_gating=False,
        log_level="DEBUG",
        log_format="json",
        enabled_plugins=["research.citations"],
    )
    configure_logging(config)
    scan_call = AIMessage(
        content="",
        tool_calls=[
            {
                "name": scan_numeric.name,
                "args": {
                    "manuscript_text": "private manuscript [1]",
                    "bibliography": ["private bibliography"],
                },
                "id": "scan-test",
            }
        ],
    )
    model = ToolCallingModel(responses=[scan_call, AIMessage(content="private answer")])
    if delegate:
        worker_model = model
        worker_name = "test-worker"
        plugin = SimpleNamespace(
            manifest=PluginManifest(
                plugin_id="test.workflow", version="0.1.0", description="Test plugin"
            ),
            build=lambda services: PluginContribution(
                subagents=[
                    {
                        "name": worker_name,
                        "description": "Test worker",
                        "system_prompt": "Use the scan tool.",
                        "model": worker_model,
                        "tools": [scan_numeric],
                    }
                ]
            ),
        )
        monkeypatch.setattr(compose, "discover_plugins", lambda allowlist: [plugin])
        model = ToolCallingModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "task",
                            "args": {"subagent_type": worker_name, "description": "private task"},
                            "id": "delegate-test",
                        }
                    ],
                ),
                AIMessage(content="private answer"),
            ]
        )
    for response in model.responses + (worker_model.responses if delegate else []):
        response.usage_metadata = {
            "input_tokens": 10, "output_tokens": 5, "total_tokens": 15,
            "input_token_details": {"cache_read": 4},
        }
    monkeypatch.setattr(compose, "init_configured_model", lambda settings: model)
    callback = WorkflowLoggingHandler()
    run_config = {"configurable": {"thread_id": "test-thread"}, "callbacks": [callback]}
    with log_context("test-run"), open_store(config) as store:
        agent = build_agent(config, store=store)
        inputs = {"messages": [{"role": "user", "content": "private prompt"}]}
        context = RunContext(tenant_id="private tenant", user_id="private user")
        if async_mode:
            result = asyncio.run(agent.ainvoke(inputs, config=run_config, context=context))
        else:
            result = agent.invoke(inputs, config=run_config, context=context)

    assert result["messages"][-1].content == "private answer"
    calls = 4 if delegate else 2
    assert callback.usage == {
        "model_calls": calls, "usage_reported_calls": calls, "complete": True,
        "input_tokens": calls * 10, "output_tokens": calls * 5, "total_tokens": calls * 15,
        "input_token_details": {"cache_read": calls * 4},
    }
    output = capsys.readouterr().err
    assert "private" not in output
    logged = [json.loads(line) for line in output.splitlines()]
    events = {item["event"] for item in logged}
    assert {
        "agent_build_started",
        "agent_build_completed",
        "tool_registered" if not delegate else "subagent_registered",
        "chain_started",
        "chain_completed",
        "model_started",
        "model_completed",
        "tool_started",
        "tool_completed",
        "citation_parsing_completed",
        "citation_evaluation_completed",
    } <= events
    operations = [item for item in logged if "operation_id" in item]
    assert all(item["run_id"] == "test-run" for item in operations)
    assert all(
        item["parent_operation_id"] for item in operations if item["event"] == "model_started"
    )
    started = {item["operation_id"] for item in operations if item["event"].endswith("_started")}
    completed = {
        item["operation_id"] for item in operations if item["event"].endswith("_completed")
    }
    assert started == completed
    assert all(
        item["duration_ms"] >= 0 for item in operations if item["event"].endswith("_completed")
    )


def test_usage_marks_missing_provider_metadata_and_errors_incomplete():
    from langchain_core.outputs import ChatGeneration, LLMResult

    callback = WorkflowLoggingHandler()
    run_id = uuid4()
    callback.on_chat_model_start({}, [], run_id=run_id)
    callback.on_llm_end(LLMResult(generations=[[ChatGeneration(message=AIMessage(content="ok"))]]), run_id=run_id)
    assert callback.usage["model_calls"] == 1
    assert not callback.usage["complete"]
    callback.on_chat_model_start({}, [], run_id=run_id)
    callback.on_llm_error(RuntimeError("private error"), run_id=run_id)
    assert callback.usage["model_calls"] == 2
    assert callback.usage["usage_reported_calls"] == 0


@pytest.mark.parametrize("kind", ["chain", "llm", "tool"])
@pytest.mark.parametrize(
    "error,event,level",
    [
        (RuntimeError("private failure"), "failed", "ERROR"),
        (GraphInterrupt(()), "suspended", "DEBUG"),
        (CancelledError(), "cancelled", "WARNING"),
    ],
)
def test_callbacks_distinguish_failure_suspension_and_cancellation(
    kind, error, event, level, capsys
):
    configure_logging(
        HarnessSettings(_env_file=None, model="test:model", log_level="DEBUG", log_format="json")
    )
    callback = WorkflowLoggingHandler()
    run_id = uuid4()
    getattr(callback, f"on_{kind}_start")(
        {"name": "test-component"}, "private input", run_id=run_id
    )
    getattr(callback, f"on_{kind}_error")(error, run_id=run_id)
    output = capsys.readouterr().err
    assert "private" not in output
    logged = [json.loads(line) for line in output.splitlines()]
    expected_kind = "model" if kind == "llm" else kind
    assert logged[-1]["event"] == f"{expected_kind}_{event}"
    assert logged[-1]["level"] == level
    assert logged[-1]["duration_ms"] >= 0


def test_tool_error_result_and_retry_are_logged(capsys):
    configure_logging(
        HarnessSettings(_env_file=None, model="test:model", log_level="DEBUG", log_format="json")
    )
    callback = WorkflowLoggingHandler()
    run_id = uuid4()
    callback.on_tool_start({"name": "test-tool"}, "private input", run_id=run_id)
    callback.on_retry(SimpleNamespace(attempt_number=2), run_id=run_id)
    callback.on_tool_end(
        ToolMessage(content="private error", tool_call_id="test", status="error"), run_id=run_id
    )
    output = capsys.readouterr().err
    assert "private" not in output
    logged = [json.loads(line) for line in output.splitlines()]
    assert logged[-2]["event"] == "operation_retrying"
    assert logged[-2]["attempt"] == 2
    assert logged[-1]["event"] == "tool_failed"
    assert "error_type" not in logged[-1]


def test_failed_skill_upload_stops_build_without_logging_false_success(
    tmp_path, monkeypatch, capsys
):
    config = HarnessSettings(
        _env_file=None,
        model="test:model",
        workspace_root=tmp_path,
        store_backend="memory",
        log_level="DEBUG",
        log_format="json",
        enabled_plugins=["research.citations"],
    )
    configure_logging(config)
    backend = SimpleNamespace(
        upload_files=Mock(
            return_value=[
                FileUploadResponse(path="/skills/test/SKILL.md", error="permission_denied"),
            ]
        )
    )
    monkeypatch.setattr(compose, "compose_backend", lambda *args, **kwargs: backend)
    compile_agent = Mock()
    monkeypatch.setattr(compose, "create_deep_agent", compile_agent)
    with pytest.raises(RuntimeError, match="skill assets could not be uploaded"):
        build_agent(config)
    compile_agent.assert_not_called()
    logged = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    assert logged[-1]["event"] == "skill_asset_upload_failed"
    assert "skill_assets_uploaded" not in {item["event"] for item in logged}
