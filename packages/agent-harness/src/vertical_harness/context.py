"""Trusted per-run identity, supplied by the host."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class RunContext:
    """每次智能体运行由 LangGraph 注入的可信运行时上下文。"""

    tenant_id: str
    user_id: str
    task_tier: Literal["economy", "quality"] = "quality"

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, str) or not value.strip()
            for value in (self.tenant_id, self.user_id)
        ):
            raise ValueError("RunContext requires nonempty trusted tenant_id and user_id")
        if self.task_tier not in {"economy", "quality"}:
            raise ValueError("task_tier must be economy or quality")
