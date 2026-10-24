"""智能体核心业务领域包。

本领域包负责核心智能体图结构编排、隔离上下文注入与动态模型路由：
1. `AgentComposer`: 基于 LangGraph `create_react_agent` 组装包含中间件链路、安全审查与系统提示词的 Agent 实例；
2. `ModelRouting`: 依据任务层级（Tier）动态切换和初始化底层 ChatModel 实例；
3. `RunContext`: 承载租户标识、用户标识与工作区边界的不可变运行上下文实体。
"""

from vertical_harness.core.compose import AgentComposer
from vertical_harness.core.context import RunContext
from vertical_harness.core.routing import ModelRouting

__all__ = [
    "AgentComposer",
    "ModelRouting",
    "RunContext",
]
