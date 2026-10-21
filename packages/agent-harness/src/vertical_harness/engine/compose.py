"""向后兼容的智能体编排引擎门面模块。"""

from __future__ import annotations

from vertical_harness.core.compose import (
    BASE_PROMPT,
    AgentComposer,
)
from vertical_harness.core.routing import ModelRouting


class EngineComposer(AgentComposer):
    """向后兼容的智能体编排器门面。

    :author: tongliga
    :date: 2026-10-21
    """


_summarization_middleware = AgentComposer._summarization_middleware
build_agent = AgentComposer.build_agent
open_agent = AgentComposer.open_agent
aopen_agent = AgentComposer.aopen_agent

__all__ = [
    "BASE_PROMPT",
    "AgentComposer",
    "EngineComposer",
    "ModelRouting",
    "_summarization_middleware",
    "aopen_agent",
    "build_agent",
    "open_agent",
]
