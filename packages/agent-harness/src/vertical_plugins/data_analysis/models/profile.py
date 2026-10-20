"""表格统计画像强类型 Pydantic 数据契约模型模块。"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from vertical_plugins.common.contracts import StrictModel


class TableProfileInput(StrictModel):
    """请求执行表格画像时的输入参数模型。"""

    file_path: Annotated[str, Field(min_length=1, description="待分析数据表的本地路径")]
    sheet_name: str | None = Field(default=None, description="工作表名称，若未指定则列出全部工作表")
    column_offset: int = Field(default=0, ge=0, description="从零开始的列分页偏移量")


__all__ = [
    "StrictModel",
    "TableProfileInput",
]
