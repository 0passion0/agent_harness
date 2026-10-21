from enum import StrEnum


class TaskTier(StrEnum):
    """任务质量与计算预算路由层级枚举。"""

    ECONOMY = "economy"
    QUALITY = "quality"
