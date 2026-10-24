"""生产执行入口的关联、隔离和脱敏测试；不访问 LangSmith 网络。"""

import asyncio
import json
import logging
from contextlib import aclosing
from uuid import uuid4

import pytest
from langchain_core.callbacks import BaseCallbackHandler, CallbackManager
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Command

from test_deepagents_architecture import Model, build, call, settings
from vertical_harness.config.trace_redaction import TraceRedaction
from vertical_harness.langchain.callbacks import WorkflowLoggingHandler
from vertical_harness.langchain.streaming import AgentStreaming
from vertical_harness.log.logging import HarnessLogging
from vertical_harness.models import LangSmithSettings
from vertical_harness.tools.search import WebSearch


class Observer(BaseCallbackHandler):
    run_inline = True

    def __init__(self):
        self.calls = 0

    def on_chat_model_start(self, *args, **kwargs):
        self.calls += 1


@pytest.mark.parametrize("streaming", [False, True])
def test_production_entry_records_isolated_metrics_and_preserves_caller_callbacks(
    tmp_path, monkeypatch, capsys, streaming
):
    HarnessLogging.configure_logging(settings(tmp_path, log_format="json"))
    tokens = {"input_tokens": 8, "output_tokens": 3, "total_tokens": 11}
    agent = build(
        tmp_path, monkeypatch, Model(responses=[AIMessage("PRIVATE_ANSWER", usage_metadata=tokens)])
    )
    observer = Observer()
    manager = CallbackManager([observer], inheritable_handlers=[observer])
    configs = [
        {"run_id": uuid4(), "configurable": {"thread_id": f"thread-{i}"}, "callbacks": manager}
        for i in range(16)
    ]

    async def run(config):
        prompt = f"PRIVATE_PROMPT_{config['configurable']['thread_id']}"
        if streaming:
            async with aclosing(
                AgentStreaming.astream_agent(
                    agent, {"messages": [HumanMessage(prompt)]}, config=config
                )
            ) as events:
                assert [e async for e in events][-1].type == "run.completed"
        else:
            result = await AgentStreaming.ainvoke_agent(
                agent, {"messages": [HumanMessage(prompt)]}, config=config
            )
            assert result["messages"][-1].content == "PRIVATE_ANSWER"
        state = await agent.aget_state(config)
        assert [m.content for m in state.values["messages"] if isinstance(m, HumanMessage)] == [
            prompt
        ]

    async def concurrent():
        await asyncio.gather(*(run(c) for c in configs))

    asyncio.run(concurrent())
    raw = capsys.readouterr().err
    assert "PRIVATE" not in raw
    logs = [json.loads(line) for line in raw.splitlines()]
    summaries = [
        e for e in logs if e["event"] == "chain_completed" and e.get("parent_operation_id") is None
    ]
    assert len(summaries) == 16
    assert {(s["run_id"], s["thread_id"]) for s in summaries} == {
        (str(c["run_id"]), c["configurable"]["thread_id"]) for c in configs
    }
    assert all(
        s["model_calls"] == 1 and s["model_rounds"] == 1 and s["total_tokens"] == 11
        for s in summaries
    )
    assert all(s["tool_calls"] == 0 and s["duration_ms"] >= 0 for s in summaries)
    assert manager.handlers == [observer]
    assert observer.calls == 16


def test_command_tool_error_counts_as_failure_and_interrupt_does_not():
    from langgraph.errors import GraphInterrupt

    handler = WorkflowLoggingHandler()
    run_id = uuid4()
    handler.on_tool_start({}, "private", run_id=run_id)
    handler.on_tool_error(GraphInterrupt(), run_id=run_id)
    handler.on_tool_start({}, "private", run_id=run_id)
    handler.on_tool_end(
        Command(
            update={
                "messages": [ToolMessage(content="private", tool_call_id="call", status="error")]
            }
        ),
        run_id=run_id,
    )
    assert handler.usage["tool_errors"] == 1
    assert handler.usage["tool_calls"] == 2
    assert handler.usage["tool_error_rate"] == 0.5


@pytest.mark.parametrize("serialized", [False, True])
def test_trace_redaction_preserves_usage_and_identifiers_without_payload(serialized):
    message = {
        "content": "PRIVATE_OUTPUT",
        "tool_calls": [{"args": {"secret": "PRIVATE_TOKEN"}}],
        "usage_metadata": {"input_tokens": 8, "output_tokens": 3, "total_tokens": 11},
    }
    if serialized:
        message = {"kwargs": message, "id": ["AIMessage"]}
    payload = {"generations": [[{"message": message, "text": "PRIVATE_OUTPUT"}]]}
    assert TraceRedaction.anonymize(payload) == {
        "llm_output": {
            "token_usage": {"prompt_tokens": 8, "completion_tokens": 3, "total_tokens": 11}
        }
    }
    assert TraceRedaction.anonymize(
        {
            "thread_id": "thread",
            "ls_model_name": "test",
            "user_id": "PRIVATE_USER",
            "api_key": "PRIVATE_TOKEN",
        }
    ) == {"thread_id": "thread", "ls_model_name": "test"}
    assert TraceRedaction.anonymize({"error": "PRIVATE_ERROR"}) == {
        "error": "Error details omitted"
    }
    assert payload["generations"][0][0]["text"] == "PRIVATE_OUTPUT"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"messages": ["PRIVATE"]},
        {"generations": "PRIVATE"},
        {"generations": [[None]]},
        {"generations": [[{"message": "PRIVATE"}]]},
        {"llm_output": {"token_usage": "PRIVATE"}},
        {
            "llm_output": {
                "token_usage": {
                    "prompt_tokens": -1,
                    "completion_tokens": True,
                    "total_tokens": "PRIVATE",
                }
            }
        },
    ],
)
def test_trace_redaction_does_not_trust_arbitrary_tool_results(payload):
    assert TraceRedaction.anonymize(payload) == {}


def test_trace_keeps_cached_and_reasoning_counts_without_free_form_fields():
    payload = {
        "generations": [
            [
                {
                    "message": {
                        "usage_metadata": {
                            "input_tokens": 20,
                            "output_tokens": 8,
                            "total_tokens": 28,
                            "input_token_details": {"cache_read": 10, "private": "PRIVATE"},
                            "output_token_details": {"reasoning": 5, "private": "PRIVATE"},
                        }
                    }
                }
            ]
        ]
    }
    usage = TraceRedaction.anonymize(payload)["llm_output"]["token_usage"]
    assert usage["prompt_tokens_details"] == {"cached_tokens": 10}
    assert usage["completion_tokens_details"] == {"reasoning_tokens": 5}
    assert "PRIVATE" not in json.dumps(usage)


def test_real_langsmith_client_transforms_uploads_without_network(monkeypatch):
    from langsmith import Client
    from requests import Response

    captured = []

    def request(self, *args, **kwargs):
        captured.append(json.loads(kwargs["request_kwargs"]["data"]))
        response = Response()
        response.status_code = 200
        response._content = b"{}"
        return response

    monkeypatch.setattr(Client, "request_with_retries", request)
    config = LangSmithSettings(_env_file=None, api_key="test")
    client = config.create_client(auto_batch_tracing=False)
    try:
        client.create_run(
            "safe-operation",
            {"prompt": "PRIVATE_INPUT"},
            "llm",
            outputs={
                "llm_output": {
                    "token_usage": {"prompt_tokens": 8, "completion_tokens": 3, "total_tokens": 11}
                },
                "answer": "PRIVATE_OUTPUT",
            },
            error="PRIVATE_ERROR",
            extra={
                "metadata": {
                    "thread_id": "thread",
                    "api_key": "PRIVATE_TOKEN",
                    "ls_model_name": "test",
                }
            },
        )
    finally:
        client.close()
    assert captured and "PRIVATE" not in json.dumps(captured)
    assert captured[0]["inputs"] == {}
    assert captured[0]["outputs"]["llm_output"]["token_usage"]["total_tokens"] == 11
    assert captured[0]["extra"]["metadata"]["thread_id"] == "thread"


def test_real_graph_trace_links_model_to_run_and_thread_without_prompt_upload(
    tmp_path, monkeypatch
):
    from langchain_core.tracers.langchain import wait_for_all_tracers
    from langsmith import Client, tracing_context
    from requests import Response

    captured = []

    def request(self, *args, **kwargs):
        body = kwargs.get("request_kwargs", kwargs).get("data")
        if body:
            captured.append(json.loads(body))
        response = Response()
        response.status_code = 200
        response._content = b"{}"
        return response

    monkeypatch.setattr(Client, "request_with_retries", request)
    tokens = {"input_tokens": 8, "output_tokens": 3, "total_tokens": 11}
    graph = build(
        tmp_path, monkeypatch, Model(responses=[AIMessage("safe", usage_metadata=tokens)])
    )
    run_id = uuid4()
    client = LangSmithSettings(_env_file=None, api_key="test").create_client(
        auto_batch_tracing=False
    )
    try:
        with tracing_context(enabled=True, client=client, project_name="offline-test"):
            asyncio.run(
                AgentStreaming.ainvoke_agent(
                    graph,
                    {"messages": [HumanMessage("PRIVATE_PROMPT")]},
                    config={"run_id": run_id, "configurable": {"thread_id": "trace-thread"}},
                )
            )
            wait_for_all_tracers()
            client.flush()
    finally:
        client.close()
    assert captured and "PRIVATE_PROMPT" not in json.dumps(captured)
    root = next(r for r in captured if r.get("id") == str(run_id) and r.get("run_type") == "chain")
    assert root["trace_id"] == str(run_id)
    assert root["extra"]["metadata"]["thread_id"] == "trace-thread"
    model = next(r for r in captured if r.get("run_type") == "llm")
    assert model["trace_id"] == str(run_id) and model["parent_run_id"] is not None
    assert model["extra"]["metadata"]["thread_id"] == "trace-thread"
    assert any(
        r.get("outputs", {}).get("llm_output", {}).get("token_usage", {}).get("total_tokens") == 11
        for r in captured
    )


def test_search_error_is_a_tool_error_in_real_graph(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(WebSearch, "search_api_key", lambda: "")
    model = Model(
        responses=[
            AIMessage(content="", tool_calls=[call("web_search", {"query": "evidence"})]),
            AIMessage("No sources available"),
        ]
    )
    agent = build(tmp_path, monkeypatch, model)
    with caplog.at_level(logging.INFO):
        asyncio.run(
            AgentStreaming.ainvoke_agent(
                agent,
                {"messages": [HumanMessage("search")]},
                config={"configurable": {"thread_id": "search"}},
            )
        )
    result = next(m for m in model.seen[-1] if isinstance(m, ToolMessage))
    assert result.status == "error" and "EXA_API_KEY" in result.content
    summary = next(
        r.event_fields
        for r in caplog.records
        if r.msg == "chain_completed" and "model_calls" in r.event_fields
    )
    assert summary["tool_errors"] == 1
