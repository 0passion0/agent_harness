"""Typed contribution contracts for installed domain plugins."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Protocol

from deepagents import CompiledSubAgent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.tools import BaseTool
from langgraph.store.base import BaseStore
from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    """Forbid unknown contract fields."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)


class SideEffect(StrEnum):
    """Tool permission classification."""

    READ = "read"
    WRITE = "write"
    EXTERNAL = "external"


class Exposure(StrEnum):
    """Control when a tool becomes model-visible."""

    EAGER = "eager"
    SKILL = "skill"


class PluginManifest(StrictModel):
    """Stable metadata for a separately installed plugin."""

    plugin_id: str = Field(pattern=r"^[a-z][a-z0-9_.-]+$")
    api_version: int = Field(default=1, ge=1, le=1)
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    description: str = Field(min_length=8)


class SkillAsset(StrictModel):
    """Files bundled in a plugin and mounted under /skills at boot."""

    relative_path: str
    content: str

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        """Reject filesystem traversal and absolute paths."""
        path = Path(value)
        if path.is_absolute() or any(part in {".", ".."} for part in path.parts) or len(path.parts) < 2:
            raise ValueError("Skill path must be a relative path inside a skill directory")
        if any(not part.replace("-", "").replace("_", "").replace(".", "").isalnum() for part in path.parts):
            raise ValueError("Invalid skill path component")
        return value


class ToolContribution(StrictModel):
    """Registered LangChain tool and exposure/approval metadata."""

    tool: BaseTool
    effect: SideEffect = SideEffect.READ
    exposure: Exposure = Exposure.EAGER


class PluginContribution(StrictModel):
    """All native framework contributions of a plugin."""

    tools: list[ToolContribution] = Field(default_factory=list)
    skills: list[SkillAsset] = Field(default_factory=list)
    middleware: list[AgentMiddleware] = Field(default_factory=list)
    subagents: list[dict[str, Any] | CompiledSubAgent] = Field(default_factory=list)
    instructions: str = ""


@dataclass(frozen=True)
class BuildServices:
    """Host-provided build-time dependencies; no global singleton."""

    workspace_root: Path
    store: BaseStore | None = None


@dataclass(frozen=True)
class RunContext:
    """Run-scoped values injected by LangGraph, not by model-generated args."""

    tenant_id: str
    user_id: str
    task_tier: Literal["economy", "quality"] = "quality"


class DomainPlugin(Protocol):
    """Python entry point plugin protocol."""

    manifest: PluginManifest

    def build(self, services: BuildServices) -> PluginContribution:
        """Create this plugin's native Deep Agents/LangChain contributions."""
        ...
