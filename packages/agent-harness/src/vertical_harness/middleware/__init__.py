"""智能体执行中间件领域包。

本领域包提供拦截、过滤与保护智能体调用的中间件管线集合：
1. `DesktopApprovalMiddleware`: 针对具副作用的危险工具调用实施人机回环中断与审批；
2. `ContextLimitMiddleware`: 监控大模型上下文窗口容量并在超限时提供优雅容错；
3. `DesktopPermissionMiddleware`: 严格只读（`read_only`）沙箱模式下的工具白名单过滤与越权拦截；
4. `ModelRouterMiddleware`: 根据任务复杂度与动态提示分级切换调用模型的中间件。
"""

from vertical_harness.middleware.approval import DesktopApprovalMiddleware
from vertical_harness.middleware.context_limit import ContextLimitMiddleware
from vertical_harness.middleware.permission import DesktopPermissionMiddleware
from vertical_harness.middleware.routing import ModelRouterMiddleware

__all__ = [
    "ContextLimitMiddleware",
    "DesktopApprovalMiddleware",
    "DesktopPermissionMiddleware",
    "ModelRouterMiddleware",
]
