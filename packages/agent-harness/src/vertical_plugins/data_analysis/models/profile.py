"""表格统计画像强类型 Pydantic 数据契约模型模块。"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    """禁止未声明字段的严格基础契约模型。"""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)


class ColumnValueCount(StrictModel):
    """特定列高频或罕见词法值统计项。"""

    value: str
    length: int
    count: int


class NumericSubset(StrictModel):
    """数值型列在有限实数子集上的聚合统计描述。"""

    min: float | None = None
    max: float | None = None
    mean: float | None = None
    std: float | None = None
    approx_quartiles: list[float] | None = None


class ColumnProfile(StrictModel):
    """单列字段的全量行扫描统计画像。"""

    name: str
    storage_type: str
    missing_count: int
    distinct_estimate: int
    numeric_count: int
    nonfinite_count: int
    non_numeric_count: int
    leading_zero_count: int
    numeric_subset: NumericSubset | None = None
    frequent_values: list[ColumnValueCount] | None = None
    rare_values: list[ColumnValueCount] | None = None
    value_counts: str | None = None
    name_truncated: bool = False


class TableProfileInput(StrictModel):
    """请求执行表格画像时的输入参数模型。"""

    file_path: Annotated[str, Field(min_length=1, description="待分析数据表的本地路径")]
    sheet_name: str | None = Field(default=None, description="工作表名称，若未指定则列出全部工作表")
    column_offset: int = Field(default=0, ge=0, description="从零开始的列分页偏移量")


class TableProfileResult(StrictModel):
    """全表扫描后返回的受限列画像结果模型。"""

    source: str
    source_sha256: str | None = None
    sheet_name: str | None = None
    sheet_names: list[str] | None = None
    next_action: str | None = None
    row_count: int = 0
    column_count: int = 0
    formula_cells: int = 0
    coverage: str | None = None
    missing_definition: str | None = None
    profile_file: str | None = None
    column_offset: int = 0
    next_column_offset: int | None = None
    columns: list[dict[str, Any]] = Field(default_factory=list)


__all__ = [
    "ColumnProfile",
    "ColumnValueCount",
    "NumericSubset",
    "StrictModel",
    "TableProfileInput",
    "TableProfileResult",
]
