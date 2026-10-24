"""由宿主系统提供的单次智能体运行可信身份与调度上下文模块。

本模块定义了 `RunContext` 不可变数据类，用于跨中间件与工具调用传递调用方身份、
多租户隔离标识以及调度等级（Task Tier）。该上下文由宿主系统可信注入，
防止提示注入等手段伪造多租户或越权身份。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class RunContext:
    """每次智能体运行由宿主系统可信注入的运行时上下文实体。

    通过只读冻结实例确保运行时上下文不被下游中间件或工具意外或恶意篡改。

    Attributes:
        tenant_id (str): 租户唯一标识符，用于多租户资源隔离与配额审计，不可为空。
        user_id (str): 发起本次执行请求的用户唯一标识符，用于权限校验与用户级隔离，不可为空。
        task_tier (Literal["economy", "quality"]): 当前任务的调度层级，决定模型路由策略。
            - `"economy"`: 使用低消耗、高响应速度的基础模型处理简单操作；
            - `"quality"`: 使用具有强推理能力的旗舰模型处理复杂逻辑，默认为 `"quality"`。
    """

    tenant_id: str
    user_id: str
    task_tier: Literal["economy", "quality"] = "quality"

    def __post_init__(self) -> None:
        """执行上下文数据类的后置不变式校验。

        校验租户标识符与用户标识符非空且不全为空白字符，以及任务层级是否处于支持列表中。

        Raises:
            ValueError: 当 `tenant_id` 或 `user_id` 为空、全为空白字符，或类型不符时抛出。
            ValueError: 当 `task_tier` 不是 `"economy"` 或 `"quality"` 时抛出。
        """
        # 严格校验可信身份标识符非空
        if any(
            not isinstance(value, str) or not value.strip()
            for value in (self.tenant_id, self.user_id)
        ):
            raise ValueError("RunContext requires nonempty trusted tenant_id and user_id")
        # 严格校验任务调度分级枚举范围
        if self.task_tier not in {"economy", "quality"}:
            raise ValueError("task_tier must be economy or quality")
