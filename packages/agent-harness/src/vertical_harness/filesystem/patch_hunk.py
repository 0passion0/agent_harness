"""多文件补丁分块（Patch Hunk）不可变数据模型模块。

本模块定义了 `PatchHunk` 数据结构，封装单个文件维度的补丁变更声明。
支持新增文件（add）、局部行块修补（update）以及文件删除（delete）操作，
并提供可选的移动重命名路径支持。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from vertical_harness.filesystem.update_chunk import UpdateChunk


@dataclass(frozen=True)
class PatchHunk:
    """单个文件变更块数据模型，支持新增文件、删除文件或对已有文件执行按块更新。

    Attributes:
        type (Literal["add", "update", "delete"]): 补丁操作类型。
            - `"add"`: 创建新文件，内容由 `contents` 指定；
            - `"update"`: 基于现存文件应用局部 `chunks` 差异行切片；
            - `"delete"`: 删除工作区内的目标文件。
        path (str): 目标文件在工作区中的相对路径（如 `"src/index.ts"`）。
        contents (str): 当 `type="add"` 时的全新文本内容；其他操作类型下留空。
        move_path (str | None): 可选的文件移动/重命名目标相对路径；若仅就地变更则为 None。
        chunks (tuple[UpdateChunk, ...]): 当 `type="update"` 时包含的按序行更新块元组。
    """

    # 变更操作类型（add 新增、update 更新、delete 删除）
    type: Literal["add", "update", "delete"]
    # 目标文件的相对路径
    path: str
    # 新增文件时的完整文本内容
    contents: str = ""
    # 可选的目标移动/重命名路径
    move_path: str | None = None
    # update 操作下包含的局部更新差异块列表
    chunks: tuple[UpdateChunk, ...] = ()
