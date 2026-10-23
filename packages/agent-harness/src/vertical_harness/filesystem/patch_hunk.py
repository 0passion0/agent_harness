"""多文件补丁分块（Patch Hunk）不可变数据模型模块。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from vertical_harness.filesystem.update_chunk import UpdateChunk


@dataclass(frozen=True)
class PatchHunk:
    """单个文件变更块数据模型，支持新增文件、删除文件或对已有文件执行按块更新。

    :param type: 文件变更操作类型（"add" 新增，"update" 更新，"delete" 删除）。
    :type type: typing.Literal["add", "update", "delete"]
    :param path: 操作的目标文件工作区相对路径。
    :type path: str
    :param contents: 新增文件时的完整文本内容（默认空字符串）。
    :type contents: str
    :param move_path: 文件重命名或移动的目标路径（可选）。
    :type move_path: str | None
    :param chunks: 文件更新时的差异行更新块元组（默认空元组）。
    :type chunks: tuple[vertical_harness.filesystem.update_chunk.UpdateChunk, ...]
    :author: tongliga
    :date: 2026-10-21
    """

    type: Literal["add", "update", "delete"]
    path: str
    contents: str = ""
    move_path: str | None = None
    chunks: tuple[UpdateChunk, ...] = ()
