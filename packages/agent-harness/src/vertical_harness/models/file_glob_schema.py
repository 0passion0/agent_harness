"""文件通配（Glob）查询输入参数验证 Schema 模块。

本模块提供 `FileGlobSchema` Pydantic 模型，继承自 DeepAgents 的标准 `GlobSchema`。
扩展了隐藏文件包含控制与最大返回匹配条目上限约束。
"""

from __future__ import annotations

from deepagents.middleware.filesystem import GlobSchema
from pydantic import Field


class FileGlobSchema(GlobSchema):
    """文件通配参数模型，扩展隐藏文件过滤与最大返回数量约束。

    Attributes:
        path (str | None): 可选的限定起始搜索工作区相对目录路径。
        pattern (str): 文件通配符匹配表达式（如 `"**/*.py"`）。
        hidden (bool): 是否将隐藏文件（以点开头的文件）纳入检索范围（始终排除 `.git` 目录），默认 False。
        limit (int): 返回的文件路径最大数量限制（必须大于 0），默认 100。
    """

    hidden: bool = Field(
        default=False, description="Include hidden files, excluding .git directories."
    )
    limit: int = Field(default=100, gt=0, description="Maximum number of file paths to return.")
