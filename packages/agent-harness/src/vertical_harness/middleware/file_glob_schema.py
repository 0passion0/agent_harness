"""文件通配（Glob）查询输入参数验证 Schema 模块。"""

from __future__ import annotations

from deepagents.middleware.filesystem import GlobSchema
from pydantic import Field


class FileGlobSchema(GlobSchema):
    """文件通配参数模型，扩展隐藏文件过滤与最大返回数量约束。"""

    hidden: bool = Field(
        default=False, description="Include hidden files, excluding .git directories."
    )
    limit: int = Field(default=100, gt=0, description="Maximum number of file paths to return.")
