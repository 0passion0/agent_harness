"""由宿主系统提供的单次智能体运行可信身份与调度上下文模块。"""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class RunContext:
    """每次智能体运行由 LangGraph 注入的可信运行时上下文实体。

    承载宿主验证后的租户、用户标识符以及当前任务的计费或计算资源梯队。

    :param tenant_id: 租户唯一标识符，必须为非空有效字符串。
    :type tenant_id: str
    :param user_id: 用户唯一标识符，必须为非空有效字符串。
    :type user_id: str
    :param task_tier: 任务计算梯队，仅支持 'economy'（经济型）或 'quality'（高品质型），默认为 'quality'。
    :type task_tier: typing.Literal['economy', 'quality']
    :author: tongliga
    :date: 2026-10-21
    """

    tenant_id: str
    user_id: str
    task_tier: Literal["economy", "quality"] = "quality"

    def __post_init__(self) -> None:
        """实体初始化后的后置不变量断言校验。

        :return: 无返回值。
        :rtype: None
        :raises ValueError: 当 tenant_id 或 user_id 为空，或 task_tier 不合法时抛出。
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：校验租户 ID 与用户 ID 必须为非空有效字符串
        if any(
                not isinstance(value, str) or not value.strip()
                for value in (self.tenant_id, self.user_id)
        ):
            raise ValueError("RunContext requires nonempty trusted tenant_id and user_id")
        # 第二步：校验任务层级是否处于受控枚举集合内
        if self.task_tier not in {"economy", "quality"}:
            raise ValueError("task_tier must be economy or quality")
