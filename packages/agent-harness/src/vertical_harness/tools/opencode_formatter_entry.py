"""Formatter configuration entry from OpenCode 7d3752c."""

from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator


class OpenCodeFormatterEntry(BaseModel):
    model_config = ConfigDict(strict=True)

    disabled: bool = False
    command: list[str] | None = None
    environment: dict[str, str] | None = None
    extensions: list[str] | None = None

    @field_validator("command", "environment", "extensions", mode="before")
    @classmethod
    def _not_null(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("Formatter fields must not be null")
        return value
