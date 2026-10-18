"""智能体组装引擎与模型路由子包公共导出。"""

from vertical_harness.engine.compose import (
    BASE_PROMPT,
    DEFAULT_FILESYSTEM_PERMISSIONS,
    RESERVED_SUBAGENT_NAMES,
    AgentComposer,
    build_agent,
)
from vertical_harness.engine.routing import init_configured_model, make_router

__all__ = [
    "BASE_PROMPT",
    "DEFAULT_FILESYSTEM_PERMISSIONS",
    "RESERVED_SUBAGENT_NAMES",
    "AgentComposer",
    "build_agent",
    "init_configured_model",
    "make_router",
]
