"""代码格式化器配置项数据模型模块。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator


class FormatterEntry(BaseModel):
    """外部格式化工具的独立配置项，支持自定义执行命令、环境变量与作用文件扩展名。

    :author: tongliga
    :date: 2026-10-21
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

        :param value: 传入的待校验字段值。
        :type value: typing.Any
        :return: 校验通过的字段原值。
        :rtype: typing.Any
        :raises ValueError: 当字段值为显式 None 时抛出。
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：检查是否传入显式 None
        if value is None:
            raise ValueError("Formatter fields must not be null")
        # 第二步：返回合法字段值
        return value
