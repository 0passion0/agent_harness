"""用于确定性数字引用检查的 Pydantic v2 数据模式与枚举定义模块。"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator


class IssueKind(StrEnum):
    """单条引用异常分类枚举。"""

    # 正文引用序号超出文末参考文献列表范围
    OUT_OF_RANGE = "out_of_range"
    # 参考文献列表中存在但正文从未引用
    UNCITED = "uncited"


class CheckStatus(StrEnum):
    """引用序号核查总体状态枚举。"""

    # 所有引用与文献列表完全匹配
    PASSED = "passed"
    # 存在越界引用或未被引用的孤立文献
    ISSUES = "issues"
    # 手稿中未检测到任何数字方括号引用
    NOT_APPLICABLE = "not_applicable"


ManuscriptStr = Annotated[str, Field(min_length=1, max_length=200_000)]
BibliographyList = Annotated[list[str], Field(min_length=1, max_length=10_000)]
PositiveIndex = Annotated[int, Field(ge=1)]


class ReferenceCheckInput(BaseModel):
    """数字引用核查工具的输入参数数据模型。"""

    # 严格模式且禁止任何多余未声明参数传入，防止非预期 payload
    model_config = ConfigDict(extra="forbid", strict=True)

    # 包含行内引用的手稿正文纯文本，长度限制 1 到 200,000 字符
    manuscript_text: ManuscriptStr
    # 按照正文出现顺序排列的参考文献条目列表，条目数限制 1 到 10,000
    bibliography: BibliographyList

    @field_validator("manuscript_text")
    @classmethod
    def validate_numeric_citations(cls, value: str) -> str:
        """Reject unsupported numeric identifiers before tool/workflow execution."""
        from research_citation.checker.parser import extract_cited_indices

        extract_cited_indices(value)
        return value


class ReferenceIssue(BaseModel):
    """单条确定性引用序号错误描述模型。"""

    # 禁止未声明的多余字段
    model_config = ConfigDict(extra="forbid")

    # 错误类型：out_of_range（超出参考文献列表范围）或 uncited（文献列表存在但正文未引用）
    kind: IssueKind
    # 对应的引用数字索引，从 1 开始计数
    index: PositiveIndex
    # 针对该错误原因的详细说明
    explanation: str


class ReferenceCheckResult(BaseModel):
    """数字引用核验结果数据模型（不包含未经核实的科学真实性判定）。"""

    # 禁止未声明的多余字段
    model_config = ConfigDict(extra="forbid")

    # 核验状态：passed（完全匹配通过）、issues（存在错误）、not_applicable（手稿中无数字引用）
    status: CheckStatus
    # 正文中成功提取并去重后的所有有效引用数字序号列表
    cited_indices: list[int]
    # 发现的所有引用异常问题列表
    issues: list[ReferenceIssue]
    # 核验范围界定说明，提示模型本结果仅针对数字索引有效，不涉及事实与语义真实性
    verified_scope: str = "Numeric reference indexes only; not evidence or semantic validity"


__all__ = [
    "BibliographyList",
    "CheckStatus",
    "IssueKind",
    "ManuscriptStr",
    "PositiveIndex",
    "ReferenceCheckInput",
    "ReferenceCheckResult",
    "ReferenceIssue",
]
