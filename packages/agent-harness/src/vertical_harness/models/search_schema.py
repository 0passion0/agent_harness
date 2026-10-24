"""工作区内容检索（grep）输入参数验证 Schema 模块。

本模块提供 `SearchSchema` Pydantic 模型，继承自 DeepAgents 的标准 `GrepSchema`。
扩展了非空模式串约束、字面量匹配模式开关与大小写敏感度控制字段。
"""

from __future__ import annotations

from deepagents.middleware.filesystem import GrepSchema
from pydantic import Field


class SearchSchema(GrepSchema):
    """文本检索参数模型，扩展字面量匹配模式与大小写敏感度配置。

    Attributes:
        pattern (str): 待检索的目标关键词或正则表达式字符串（最小长度为 1 个字符）。
        path (str | None): 可选的限定检索物理相对路径或单个文件。
        glob (str | None): 仅在满足特定通配表达式的文件内部搜索（例如 `"*.py"`）。
        max_count (int | None): 返回的最大匹配条目数量配额。
        literal (bool): 是否按字面常量文本精确匹配；若设为 False 则按 ripgrep 正则语法解析，默认 True。
        case_sensitive (bool): 是否严格区分英文字母大小写；若设为 False 则忽略大小写匹配，默认 True。
    """

    pattern: str = Field(min_length=1, description="Literal text or a ripgrep regular expression.")
    literal: bool = Field(
        default=True, description="Set false for ripgrep regular expression syntax."
    )
    case_sensitive: bool = Field(default=True, description="Set false for case-insensitive search.")
