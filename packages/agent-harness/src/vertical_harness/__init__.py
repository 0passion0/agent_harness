"""Search, file and script agent harness."""

from vertical_harness.config import HarnessSettings, StoreBackendType, TaskTier
from vertical_harness.context import RunContext
from vertical_harness.engine.callbacks import WorkflowLoggingHandler
from vertical_harness.engine.compose import BASE_PROMPT, aopen_agent, build_agent, open_agent
from vertical_harness.engine.routing import init_configured_model, make_router
from vertical_harness.engine.streaming import StreamEvent, ainvoke_agent, astream_agent
from vertical_harness.logging import configure_logging, log_context
from vertical_harness.observability import LangSmithSettings, tracing_session
from vertical_harness.storage import aopen_checkpointer, aopen_store, open_checkpointer, open_store


def make_checkpoint_config(thread_id: str):
    """Create native checkpoint configuration for a single conversation."""
    if not isinstance(thread_id, str) or not thread_id.strip():
        raise ValueError("thread_id must be nonempty")
    return {"configurable": {"thread_id": thread_id}}


__all__ = [
    "BASE_PROMPT",
    "HarnessSettings",
    "LangSmithSettings",
    "RunContext",
    "StoreBackendType",
    "StreamEvent",
    "TaskTier",
    "WorkflowLoggingHandler",
    "ainvoke_agent",
    "aopen_agent",
    "aopen_checkpointer",
    "aopen_store",
    "astream_agent",
    "build_agent",
    "configure_logging",
    "init_configured_model",
    "log_context",
    "make_checkpoint_config",
    "make_router",
    "open_agent",
    "open_checkpointer",
    "open_store",
    "tracing_session",
]
