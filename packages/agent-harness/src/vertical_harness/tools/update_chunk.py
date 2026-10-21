"""单文件差异更新块（Hunk Chunk）不可变数据模型模块。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UpdateChunk:
    """补丁更新块数据模型，记录被替换的原始行切片、目标替换行切片与上下文锚点。

    :param old_lines: 匹配替换的原始文本行元组。
    :type old_lines: tuple[str, ...]
    :param new_lines: 替换后写入的新文本行元组。
    :type new_lines: tuple[str, ...]
    :param change_context: 定位更新发生位置的上下文提示行内容（可选）。
    :type change_context: str | None
    :param end_of_file: 是否显式要求匹配至文件末尾。
    :type end_of_file: bool
    :author: tongliga
    :date: 2026-10-21
    """

    old_lines: tuple[str, ...]
    new_lines: tuple[str, ...]
    change_context: str | None = None
    end_of_file: bool = False
