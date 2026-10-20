"""使用真实 Deep Agents 图和本地假模型验证完整执行树，无需 LLM 凭据。"""

import json
from asyncio import CancelledError
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.errors import GraphInterrupt

from vertical_harness import HarnessSettings, WorkflowLoggingHandler, configure_logging


class ToolCallingModel(FakeMessagesListChatModel):
    """保留框架消息处理与 callbacks，仅替换外部模型响应。"""

    def bind_tools(self, tools, **kwargs):
        return self


def test_usage_marks_missing_provider_metadata_and_errors_incomplete():
    from langchain_core.outputs import ChatGeneration, LLMResult

    callback = WorkflowLoggingHandler()
    run_id = uuid4()
    callback.on_chat_model_start({}, [], run_id=run_id)
    callback.on_llm_end(
        LLMResult(generations=[[ChatGeneration(message=AIMessage(content="ok"))]]), run_id=run_id
    )
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
