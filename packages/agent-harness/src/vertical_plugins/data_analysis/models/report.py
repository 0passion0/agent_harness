"""科研数据分析与报告校验强类型 Pydantic 数据契约模型模块。"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field, field_validator

from vertical_plugins.common.contracts import StrictModel

AnalysisTypeLiteral = Literal["descriptive", "correlation", "group_comparison"]


class AnalysisTaskInput(StrictModel):
    """数据分析工作流节点可编辑与校验的强类型输入模型。"""

    file_path: Annotated[str, Field(min_length=1, description="工作区内的数据表路径（CSV/Parquet/XLSX）")]
    sheet_name: str | None = Field(default=None, description="XLSX 工作表名称（多工作表时必填）")
    column_offset: Annotated[int, Field(ge=0, description="列画像分页起始偏移")] = 0
    research_question: str | None = Field(default=None, description="明确的分析目标或研究问题")
    analysis_goal: str | None = Field(default=None, description="分析目标描述（与 research_question 等价）")
    analysis_type: AnalysisTypeLiteral = Field(
        default="descriptive",
        description="分析类型：descriptive（默认描述统计）、correlation（相关分析）、group_comparison（组间比较）",
    )
    target_columns: list[str] = Field(
        default_factory=list,
        description="显式指定的待分析数值列名称列表",
    )
    group_column: str | None = Field(
        default=None,
        description="组间比较（group_comparison）显式指定的分组列名称",
    )
    markdown_report: str | None = Field(
        default=None,
        description="可选覆写或待发布的中文 Markdown 报告正文",
    )
    evidence_files: list[str] = Field(
        default_factory=list,
        description="报告关联的脚本与 JSON 结果凭证相对路径列表",
    )
    task_type: str | None = Field(
        default=None,
        description="可选任务模式标识（full_analysis、profile、validate_report）",
    )

    @field_validator("file_path")
    @classmethod
    def _strip_file_path(cls, value: str) -> str:
        """去除文件路径首尾空白并禁止空路径。

        :param value: 原始文件路径字符串。
        :type value: str
        :return: 去空白后的路径字符串。
        :rtype: str
        :raises ValueError: 路径为空白时抛出。
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：校验非空白路径
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("file_path must not be blank")
        return cleaned


class ReportValidationInput(StrictModel):
    """科研报告校验与发布请求输入参数模型。"""

    markdown: Annotated[
        str, Field(min_length=10, description="包含核心二级标题的完整中文 Markdown 报告正文")
    ]
    evidence_files: Annotated[
        list[str],
        Field(min_length=1, description="相对任务根目录的脚本与 JSON 结果凭证文件路径列表"),
    ]


class ReportValidationResult(StrictModel):
    """报告校验与发布结果结构模型。"""

    status: Literal["passed", "failed", "invalid_input"]
    report_sha256: str | None = None
    evidence_sha256: dict[str, str] = Field(default_factory=dict)
    report_path: str | None = None
    error: str | None = None
    summary: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "AnalysisTaskInput",
    "AnalysisTypeLiteral",
    "ReportValidationInput",
    "ReportValidationResult",
    "StrictModel",
]
