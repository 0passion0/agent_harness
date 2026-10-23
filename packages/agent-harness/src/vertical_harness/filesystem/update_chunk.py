"""单文件差异更新块（Hunk Chunk）不可变数据模型模块。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UpdateChunk:
    """补丁更新块数据模型，记录被替换的原始行切片、目标替换行切片与上下文锚点。"""

    old_lines: tuple[str, ...]
    new_lines: tuple[str, ...]
    change_context: str | None = None
    end_of_file: bool = False
