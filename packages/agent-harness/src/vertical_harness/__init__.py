"""Vertical Harness 核心包公共接口导出。

架构子包划分：
- `vertical_harness.config`: 基于 Pydantic Settings 的宿主环境配置与路由规则校验
- `vertical_harness.plugins`: 领域插件契约协议（Protocol / Pydantic）与 Entry Points 发现机制
- `vertical_harness.storage`: LangGraph 原生 SqliteStore 生命周期与 Deep Agents CompositeBackend 挂载
- `vertical_harness.engine`: LangChain 聊天模型路由中间件与 Deep Agents 唯一组装器
"""

from logging import NullHandler, getLogger

from vertical_harness.config import HarnessSettings, StoreBackendType, TaskTier
from vertical_harness.engine import (
    BASE_PROMPT,
    AgentComposer,
    WorkflowLoggingHandler,
    build_agent,
    init_configured_model,
    make_router,
)
from vertical_harness.logging import configure_logging, log_context
from vertical_harness.plugins import (
    PLUGIN_GROUP,
    BuildServices,
    DomainPlugin,
    Exposure,
    PluginContribution,
    PluginManifest,
    RunContext,
    SideEffect,
    SkillAsset,
    ToolContribution,
    discover_plugins,
)
from vertical_harness.storage import (
    AsyncSqliteStore,
    SqliteStore,
    aopen_store,
    compose_backend,
    create_store,
    open_store,
    tenant_memory_namespace,
)

getLogger(__name__).addHandler(NullHandler())

__all__ = [
    "BASE_PROMPT",
    "PLUGIN_GROUP",
    "AgentComposer",
    "AsyncSqliteStore",
    "BuildServices",
    "DomainPlugin",
    "Exposure",
    "HarnessSettings",
    "PluginContribution",
    "PluginManifest",
    "RunContext",
    "SideEffect",
    "SkillAsset",
    "SqliteStore",
    "StoreBackendType",
    "TaskTier",
    "ToolContribution",
    "WorkflowLoggingHandler",
    "aopen_store",
    "build_agent",
    "compose_backend",
    "configure_logging",
    "create_store",
    "discover_plugins",
    "init_configured_model",
    "log_context",
    "make_router",
    "open_store",
    "tenant_memory_namespace",
]
