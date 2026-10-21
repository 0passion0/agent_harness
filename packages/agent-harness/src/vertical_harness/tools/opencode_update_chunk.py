"""Patch data model ported from OpenCode 7d3752c (MIT)."""

from dataclasses import dataclass


@dataclass(frozen=True)
class UpdateChunk:
    old_lines: tuple[str, ...]
    new_lines: tuple[str, ...]
    change_context: str | None = None
    end_of_file: bool = False
