"""由宿主系统提供的单次智能体运行可信身份与调度上下文模块。"""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class RunContext:
    """每次智能体运行由 LangGraph 注入的可信运行时上下文实体。"""

    tenant_id: str
    user_id: str
    task_tier: Literal["economy", "quality"] = "quality"

    def __post_init__(self) -> None:
        """实体初始化后的后置不变量断言校验。"""
        if any(
            not isinstance(value, str) or not value.strip()
            for value in (self.tenant_id, self.user_id)
        ):
            raise ValueError("RunContext requires nonempty trusted tenant_id and user_id")
        if self.task_tier not in {"economy", "quality"}:
            raise ValueError("task_tier must be economy or quality")
