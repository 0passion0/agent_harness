"""Pydantic-validated application configuration."""

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class HarnessSettings(BaseSettings):
    """Settings loaded strictly from VA_* environment variables and .env."""

    model_config = SettingsConfigDict(
        env_prefix="VA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    model: str = Field(min_length=1)
    api_key: SecretStr | None = None
    base_url: str | None = None
    economy_model: str | None = Field(default=None, min_length=1)
    quality_model: str | None = Field(default=None, min_length=1)
    workspace_root: Path = Path("./.agent-workspace")
    store_backend: Literal["sqlite", "memory"] = "sqlite"
    store_db_path: Path | Literal[":memory:"] | None = None
    memory_route: str = Field(default="/memories/", pattern=r"^/[a-zA-Z0-9_\-]+/$")
    enabled_plugins: list[str] = Field(default_factory=lambda: ["research.citations"])
    enable_planning: bool = True
    enable_skill_gating: bool = True
    max_model_calls: int = Field(default=30, ge=1, le=500)

    @property
    def resolved_store_target(self) -> Path | Literal[":memory:"]:
        """Resolve the SQLite database path or in-memory URI."""
        if self.store_db_path is not None:
            if str(self.store_db_path) == ":memory:":
                return ":memory:"
            return Path(self.store_db_path).expanduser().resolve()
        return self.workspace_root.expanduser().resolve() / "store.sqlite"

