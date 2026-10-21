"""Patch data model ported from OpenCode 7d3752c (MIT)."""

from dataclasses import dataclass
from typing import Literal

from .opencode_update_chunk import UpdateChunk


@dataclass(frozen=True)
class PatchHunk:
    type: Literal["add", "update", "delete"]
    path: str
    contents: str = ""
    move_path: str | None = None
    chunks: tuple[UpdateChunk, ...] = ()
