"""科学计算执行凭证与状态持久化 Pydantic 模型模块。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    """禁止未声明字段的严格基础契约模型。"""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)


class ExecutionReceipt(StrictModel):
    """单个 Python 科学计算脚本的真实执行凭证记录。"""

    execution_id: str
    script: str
    script_sha256: str
    args: list[Any] = Field(default_factory=list)
    artifact_sha256: dict[str, str] = Field(default_factory=dict)
    exit_code: int | None = None
    stdout_file: str | None = None
    stderr_file: str | None = None
    timed_out: bool = False
    interrupted: bool = False
    output: str | None = None


class AnalysisStateModel(StrictModel):
    """随检查点持久化的科研数据分析状态。"""

    analysis_executions: list[ExecutionReceipt] = Field(default_factory=list)
    analysis_report_path: str | None = None


__all__ = [
    "AnalysisStateModel",
    "ExecutionReceipt",
    "StrictModel",
]
