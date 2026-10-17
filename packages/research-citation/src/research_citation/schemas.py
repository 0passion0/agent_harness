"""Pydantic v2 schema for deterministic numeric reference checking."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ReferenceCheckInput(BaseModel):
    """Input to the numeric-reference verification tool."""

    model_config = ConfigDict(extra="forbid", strict=True)

    manuscript_text: str = Field(min_length=1, max_length=200_000)
    bibliography: list[str] = Field(min_length=1, max_length=10_000)


class ReferenceIssue(BaseModel):
    """One deterministic reference error."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["out_of_range", "uncited"]
    index: int = Field(ge=1)
    explanation: str


class ReferenceCheckResult(BaseModel):
    """Validation result without unsupported scientific claims."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["passed", "issues", "not_applicable"]
    cited_indices: list[int]
    issues: list[ReferenceIssue]
    verified_scope: str = "Numeric reference indexes only; not evidence or semantic validity"
