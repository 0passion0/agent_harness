"""向后兼容的流式执行引擎门面模块。"""

from __future__ import annotations

from vertical_harness.langchain.streaming import (
    AgentStreaming,
    StreamEvent,
    _project,
    _run_timeout,
    ainvoke_agent,
    astream_agent,
)


class EngineStreaming(AgentStreaming):
    """向后兼容的流式引擎门面。

    :author: tongliga
    :date: 2026-10-21
    """


__all__ = [
    "AgentStreaming",
    "EngineStreaming",
    "StreamEvent",
    "_project",
    "_run_timeout",
    "ainvoke_agent",
    "astream_agent",
]
