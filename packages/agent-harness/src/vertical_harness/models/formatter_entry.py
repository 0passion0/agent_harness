"""代码格式化器配置项数据模型模块。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator


class FormatterEntry(BaseModel):
    """外部格式化工具的独立配置项，支持自定义执行命令、环境变量与作用文件扩展名。

    Attributes:
        disabled: 是否显式禁用该格式化器，默认为 False。
        command: 外部执行命令模板参数列表（如 ['prettier', '--write', '$FILE']），为 None 时继承内置规则。
        environment: 传递给格式化器子进程的特定环境变量字典，为 None 时继承默认过滤环境。
        extensions: 该格式化器适用的文件后缀列表（如 ['.ts', '.tsx']），为 None 时匹配内置规则。
    """

    model_config = ConfigDict(strict=True)

    disabled: bool = False
    command: list[str] | None = None
    environment: dict[str, str] | None = None
    extensions: list[str] | None = None

    @field_validator("command", "environment", "extensions", mode="before")
    @classmethod
    def _not_null(cls, value: Any) -> Any:
        """校验配置字段非空约束，禁止传入显式 None 破坏执行链。

        Args:
            value: 待反序列化的原始输入数据值。

        Returns:
            Any: 原样返回的合法非 None 值。

        Raises:
            ValueError: 传入显式 null / None 时抛出。
        """
        if value is None:
            raise ValueError("Formatter fields must not be null")
        return value
