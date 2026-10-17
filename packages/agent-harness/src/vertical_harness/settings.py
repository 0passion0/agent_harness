"""Pydantic-validated application configuration."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class HarnessSettings(BaseSettings):
    """Settings loaded from VA_* environment variables."""

    model_config = SettingsConfigDict(env_prefix="VA_", extra="ignore")

    model: str = "openai:gpt-5.5"
    economy_model: str | None = None
    quality_model: str | None = None
    workspace_root: Path = Path("./.agent-workspace")
    enabled_plugins: list[str] = Field(default_factory=lambda: ["research.citations"])
    enable_planning: bool = True
    enable_skill_gating: bool = True
    max_model_calls: int = Field(default=30, ge=1, le=500)
