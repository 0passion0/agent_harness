"""代码格式化器配置项数据模型模块。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator


class FormatterEntry(BaseModel):
    """外部格式化工具的独立配置项，支持自定义执行命令、环境变量与作用文件扩展名。"""

    model_config = ConfigDict(strict=True)

    disabled: bool = False
    command: list[str] | None = None
    environment: dict[str, str] | None = None
    extensions: list[str] | None = None

    @field_validator("command", "environment", "extensions", mode="before")
    @classmethod
    def _not_null(cls, value: Any) -> Any:
        """校验配置字段非空约束，禁止传入显式 None 破坏执行链。"""
        if value is None:
            raise ValueError("Formatter fields must not be null")
        return value
