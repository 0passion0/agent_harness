"""科研报告校验与发布强类型 Pydantic 数据契约模型模块。"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    """禁止未声明字段的严格基础契约模型。"""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)


class ReportValidationInput(StrictModel):
    """科研报告校验与发布请求输入参数模型。"""

    markdown: Annotated[
        str, Field(min_length=10, description="包含七个指定二级标题的完整中文 Markdown 报告正文")
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


__all__ = [
    "ReportValidationInput",
    "ReportValidationResult",
    "StrictModel",
]
