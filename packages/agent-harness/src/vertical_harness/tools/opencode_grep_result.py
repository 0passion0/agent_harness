"""Search result retaining OpenCode's structured match metadata."""

from dataclasses import dataclass, field
from typing import Any

from deepagents.backends.protocol import GrepResult


@dataclass
class OpenCodeGrepResult(GrepResult):
    source_matches: list[dict[str, Any]] = field(default_factory=list)
