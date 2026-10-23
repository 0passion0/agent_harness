"""工作区内容检索（grep）输入参数验证 Schema 模块。"""

from __future__ import annotations

from deepagents.middleware.filesystem import GrepSchema
from pydantic import Field


class SearchSchema(GrepSchema):
    """文本检索参数模型，扩展字面量匹配模式与大小写敏感度配置。"""

    pattern: str = Field(min_length=1, description="Literal text or a ripgrep regular expression.")
    literal: bool = Field(
        default=True, description="Set false for ripgrep regular expression syntax."
    )
    case_sensitive: bool = Field(default=True, description="Set false for case-insensitive search.")
