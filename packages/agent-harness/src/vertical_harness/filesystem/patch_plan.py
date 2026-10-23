"""补丁预检产生的操作顺序、初始快照与预期最终内容。"""

from dataclasses import dataclass, field
from pathlib import Path

from vertical_harness.filesystem.patch_hunk import PatchHunk


@dataclass
class PatchPlan:
    operations: list[tuple[PatchHunk, Path, Path, bytes]] = field(default_factory=list)
    original: dict[Path, str | None] = field(default_factory=dict)
    contents: dict[Path, str | None] = field(default_factory=dict)
    names: dict[Path, str] = field(default_factory=dict)
