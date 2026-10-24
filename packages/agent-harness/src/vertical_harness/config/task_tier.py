"""模型路由质量与计算预算层级枚举定义模块。"""

from enum import StrEnum


class TaskTier(StrEnum):
    """任务质量与计算预算路由层级枚举。"""

    # 经济型低成本快速响应模型层
    ECONOMY = "economy"
    # 高品质强推理思考模型层
    QUALITY = "quality"
