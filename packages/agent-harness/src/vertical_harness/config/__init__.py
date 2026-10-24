"""应用配置领域包。

本领域包负责定义运行时所需的所有强类型配置、环境变量映射与枚举定义：
1. `HarnessSettings`: 核心配置类，涵盖模型提供商、上下文预算阈值、工具白名单与路径配置；
2. `LangSmithSettings`: 可观测性与追踪参数配置类；
3. `CheckpointConfig`: 构造带租户/用户/会话隔离元数据的 LangGraph 检查点配置字典；
4. `StoreBackendType`: 存储引擎后端类型枚举（`memory` / `sqlite`）；
5. `TaskTier`: 动态路由任务分级枚举（`fast` / `standard` / `advanced`）。
"""

from vertical_harness.config.checkpoint_config import CheckpointConfig
from vertical_harness.config.langsmith_settings import LangSmithSettings
from vertical_harness.config.settings import HarnessSettings
from vertical_harness.config.store_backend import StoreBackendType
from vertical_harness.config.task_tier import TaskTier

__all__ = [
    "CheckpointConfig",
    "HarnessSettings",
    "LangSmithSettings",
    "StoreBackendType",
    "TaskTier",
]
