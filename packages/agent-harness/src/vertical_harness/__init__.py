"""垂直 Agent 运行时微框架顶层包（Vertical Harness）。

本框架基于 LangGraph 与 LangChain 构建，专为桌面端与服务器端智能体提供完整的受控执行环境，涵盖：
1. **Core 编排与路由**：`AgentComposer` 状态图装配、`ModelRouting` 动态模型绑定与 `RunContext` 隔离上下文；
2. **Desktop 桥接**：`AgentLoop` 标准输入输出 JSON-RPC 协议解析，处理轮次启动、审批恢复与取消；
3. **安全沙箱与执行引擎**：`PythonExecution` 与受控 Shell 进程树生命周期管理；
4. **文件系统与文档感知**：`DocumentFilesystemBackend` 原子写、复合补丁（`PatchEditor`）与排版容错编辑（`TextEditor`）；
5. **防护中间件管线**：人机审批（`DesktopApprovalMiddleware`）、只读沙箱（`DesktopPermissionMiddleware`）、
   上下文限制（`ContextLimitMiddleware`）与模型分级路由（`ModelRouterMiddleware`）；
6. **存储与可观测性**：SQLite/内存持久化检查点与存储（`CheckpointFactory`、`StoreFactory`）及 LangSmith 链路追踪。
"""

from vertical_harness.bridge import AttachmentContent, DesktopMessages, DesktopRuntime
from vertical_harness.config import (
    CheckpointConfig,
    HarnessSettings,
    LangSmithSettings,
    StoreBackendType,
    TaskTier,
)
from vertical_harness.core import AgentComposer, ModelRouting, RunContext
from vertical_harness.desktop import AgentLoop
from vertical_harness.execution import PythonExecution
from vertical_harness.filesystem import DocumentFilesystemBackend
from vertical_harness.langchain import AgentStreaming, StreamEvent, WorkflowLoggingHandler
from vertical_harness.langchain.tool_details import ToolDetails
from vertical_harness.log import HarnessLogging, TracingSession
from vertical_harness.middleware import (
    ContextLimitMiddleware,
    DesktopApprovalMiddleware,
    DesktopPermissionMiddleware,
    ModelRouterMiddleware,
)
from vertical_harness.storage import CheckpointFactory, StoreFactory
from vertical_harness.tools import UserInteraction, WebSearch
from vertical_harness.utils import StructuredFormatter, UsageEstimator

__all__ = [
    "AgentComposer",
    "AgentLoop",
    "AgentStreaming",
    "AttachmentContent",
    "CheckpointConfig",
    "CheckpointFactory",
    "ContextLimitMiddleware",
    "DesktopApprovalMiddleware",
    "DesktopMessages",
    "DesktopPermissionMiddleware",
    "DesktopRuntime",
    "DocumentFilesystemBackend",
    "HarnessLogging",
    "HarnessSettings",
    "LangSmithSettings",
    "ModelRouterMiddleware",
    "ModelRouting",
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
]
