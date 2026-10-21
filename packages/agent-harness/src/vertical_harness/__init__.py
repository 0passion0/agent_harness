"""搜索、工作区文件操作与受控 Python 脚本执行智能体微框架核心模块。"""

import sys

from vertical_harness.aop import AspectInterceptor, ExecutionAspect, PointcutMatcher
from vertical_harness.bridge import (
    AttachmentContent,
    DesktopMessages,
    DesktopRuntime,
    configured_settings,
    content_blocks,
    environment_defaults,
    extract_text,
    runtime_environment,
)
from vertical_harness.config import (
    CheckpointConfig,
    HarnessSettings,
    LangSmithSettings,
    StoreBackendType,
    TaskTier,
    make_checkpoint_config,
)
from vertical_harness.core import (
    BASE_PROMPT,
    AgentComposer,
    ModelRouting,
    RunContext,
    _summarization_middleware,
    aopen_agent,
    build_agent,
    init_configured_model,
    make_router,
    open_agent,
)
from vertical_harness.desktop import AgentLoop
from vertical_harness.langchain import (
    AgentStreaming,
    StreamEvent,
    WorkflowLoggingHandler,
    _project,
    ainvoke_agent,
    astream_agent,
)
from vertical_harness.log import (
    HarnessLogging,
    TracingSession,
    configure_logging,
    log_context,
    tracing_session,
)
from vertical_harness.middleware import (
    ContextLimitMiddleware,
    DesktopApprovalMiddleware,
    DesktopPermissionMiddleware,
    ModelRouterMiddleware,
)
from vertical_harness.storage import (
    CheckpointFactory,
    DocumentFilesystemBackend,
    StoreFactory,
    aopen_checkpointer,
    aopen_store,
    open_checkpointer,
    open_store,
)
from vertical_harness.tools import (
    PythonExecution,
    ToolDetails,
    UserInteraction,
    WebSearch,
    ask_user,
    python_file_tool,
    search_api_key,
    web_search,
)
from vertical_harness.utils import (
    _RUN_ID,
    StructuredFormatter,
    UsageEstimator,
)

# 向后兼容旧版子模块导入路径
sys.modules["vertical_harness.checkpoint_config"] = sys.modules["vertical_harness.config.checkpoint_config"]
sys.modules["vertical_harness.context"] = sys.modules["vertical_harness.core.context"]
sys.modules["vertical_harness.desktop_attachments"] = sys.modules["vertical_harness.bridge.attachments"]
sys.modules["vertical_harness.desktop_messages"] = sys.modules["vertical_harness.bridge.messages"]
sys.modules["vertical_harness.desktop_runtime"] = sys.modules["vertical_harness.bridge.runtime"]
sys.modules["vertical_harness.langsmith_settings"] = sys.modules["vertical_harness.config.langsmith_settings"]
sys.modules["vertical_harness.logging"] = sys.modules["vertical_harness.log.logging"]
sys.modules["vertical_harness.observability"] = sys.modules["vertical_harness.log.observability"]
sys.modules["vertical_harness.python_execution"] = sys.modules["vertical_harness.tools.python_execution"]
sys.modules["vertical_harness.search"] = sys.modules["vertical_harness.tools.search"]
sys.modules["vertical_harness.structured_formatter"] = sys.modules["vertical_harness.utils.structured_formatter"]
sys.modules["vertical_harness.tool_details"] = sys.modules["vertical_harness.tools.tool_details"]
sys.modules["vertical_harness.usage"] = sys.modules["vertical_harness.utils.usage"]
sys.modules["vertical_harness.user_interaction"] = sys.modules["vertical_harness.tools.user_interaction"]

__all__ = [
    "BASE_PROMPT",
    "_RUN_ID",
    "AgentComposer",
    "AgentLoop",
    "AgentStreaming",
    "AspectInterceptor",
    "AttachmentContent",
    "CheckpointConfig",
    "CheckpointFactory",
    "ContextLimitMiddleware",
    "DesktopApprovalMiddleware",
    "DesktopMessages",
    "DesktopPermissionMiddleware",
    "DesktopRuntime",
    "DocumentFilesystemBackend",
    "ExecutionAspect",
    "HarnessLogging",
    "HarnessSettings",
    "LangSmithSettings",
    "ModelRouterMiddleware",
    "ModelRouting",
    "PointcutMatcher",
    "PythonExecution",
    "RunContext",
    "StoreBackendType",
    "StoreFactory",
    "StreamEvent",
    "StructuredFormatter",
    "TaskTier",
    "ToolDetails",
    "TracingSession",
    "UsageEstimator",
    "UserInteraction",
    "WebSearch",
    "WorkflowLoggingHandler",
    "_project",
    "_summarization_middleware",
    "ainvoke_agent",
    "aopen_agent",
    "aopen_checkpointer",
    "aopen_store",
    "ask_user",
    "astream_agent",
    "build_agent",
    "configure_logging",
    "configured_settings",
    "content_blocks",
    "environment_defaults",
    "extract_text",
    "init_configured_model",
    "log_context",
    "make_checkpoint_config",
    "make_router",
    "open_agent",
    "open_checkpointer",
    "open_store",
    "python_file_tool",
    "runtime_environment",
    "search_api_key",
    "tracing_session",
    "web_search",
]
