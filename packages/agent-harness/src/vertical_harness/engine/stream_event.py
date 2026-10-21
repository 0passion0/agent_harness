"""Serializable public event model emitted by AgentStreaming."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, JsonValue


class StreamEvent(BaseModel):
    """一次执行内 seq 递增；operation_id / parent_ids 标识并行、嵌套调用。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal[
        "run.started",
        "run.completed",
        "run.interrupted",
        "run.failed",
        "chain.started",
        "chain.completed",
        "chain.failed",
        "model.started",
        "model.delta",
        "model.completed",
        "tool.started",
        "tool.completed",
        "tool.failed",
        "custom.progress",
    ]
    run_id: str
    thread_id: str
    seq: int
    operation_id: str
    parent_ids: list[str]
    name: str
    agent_name: str | None = None
    node: str | None = None
    data: dict[str, JsonValue]
