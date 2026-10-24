"""单文件差异更新块（Hunk Chunk）不可变数据模型模块。

本模块定义了 `UpdateChunk` 实体，作为协同文本修补（Patching）操作中
单段替换操作的最小描述单元。通过只读元组（tuple）记录原始待剔除行集合与待置换插入行集合，
并记录可选的上下文锚点提示及是否锚定到文件尾部。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UpdateChunk:
    """补丁更新块数据模型，记录被替换的原始行切片、目标替换行切片与上下文锚点。

    Attributes:
        old_lines (tuple[str, ...]): 待在源文件中精确定位并删除的原始行序列（不含换行符）。
        new_lines (tuple[str, ...]): 待按顺序插入到原位置的目标新文本行序列（不含换行符）。
        change_context (str | None): 可选的语义上下文锚点行文本，用于在行号偏移时进行模糊定位。
        end_of_file (bool): 是否将该变更强制锚定到文件最后一行末尾。
    """

    # 待匹配并移除的原始文本行元组
    old_lines: tuple[str, ...]
    # 待插入替换的目标文本行元组
    new_lines: tuple[str, ...]
    # 定位块应用位置的上下文锚点提示
    change_context: str | None = None
    # 是否强制锚定至文件末尾
    end_of_file: bool = False
