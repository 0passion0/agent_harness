"""多文件补丁分块（Patch Hunk）不可变数据模型模块。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from vertical_harness.filesystem.update_chunk import UpdateChunk


@dataclass(frozen=True)
class PatchHunk:
    """单个文件变更块数据模型，支持新增文件、删除文件或对已有文件执行按块更新。"""

    type: Literal["add", "update", "delete"]
    path: str
    contents: str = ""
    move_path: str | None = None
    chunks: tuple[UpdateChunk, ...] = ()
