"""桌面人机协作审批中断中间件模块。

本模块提供 `DesktopApprovalMiddleware` 中间件，作为 Agent 工具执行的最后一道防御门禁：
1. 拦截有副作用的敏感操作（文件写入、编辑、删除、复合补丁、Shell 执行、Python 脚本运行及待办清单更新）；
2. 触发 LangGraph 原生 `interrupt(...)` 挂起执行图，将操作名称和入参以结构化请求形式交付前端弹窗审批；
3. 根据用户确认结果进行决策：若批准则放行至工具执行器；若拒绝则生成包含拒绝说明的标准错误 `ToolMessage`，
   安全反馈给模型以调整其后续行为。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.types import interrupt


@dataclass(eq=False)
class DesktopApprovalMiddleware(AgentMiddleware):
    """在执行文件修改或外部命令等副作用工具前触发 LangGraph 人机审批中断的中间件。

    Attributes:
        tools (list[Any]): 挂载到该中间件的工具列表（由中间件基类协议要求，默认初始化为空列表）。
    """

    tools: list[Any] = field(default_factory=list, init=False)

    @staticmethod
    def _approve(request: Any) -> ToolMessage | None:
        """检查工具是否需要审批，需要时发起 interrupt 并在拒绝时返回错误 ToolMessage。

        当工具属于高风险敏感操作列表时，调用 LangGraph 的 `interrupt` 暂停当前调用协程。
        当收到恢复指令时，解析 `decision` 决策体；若被用户拒绝，则生成对应的错误响应。

        Args:
            request (Any): 工具调用请求实体，包含 `request.tool.name` 与 `request.tool_call` 字典。

        Returns:
            ToolMessage | None: 若被用户拒绝，返回包含拒绝原因的错误工具消息；
                若无需审批或已被用户批准，则返回 None。

        Raises:
            ValueError: 当恢复传入的审批响应体结构非法或缺少决策时抛出。
        """
        name = request.tool.name
        # 仅拦截具有文件修改、补丁、删除、外部命令或待办修改副作用的敏感工具
        if name not in {
            "write_file",
            "edit_file",
            "apply_patch",
            "delete",
            "execute",
            "execute_python_file",
            "shell",
            "write_todos",
        } and not (request.tool.metadata or {}).get("requires_approval"):
            return None

        # 触发 LangGraph 原生状态图挂起中断，等待调用方提供决策输入
        decision = interrupt(
            {
                "action_requests": [
                    {
                        "name": name,
                        "args": request.tool_call.get("args", {}),
                    }
                ],
                "kind": "approval",
            }
        )

        # 严格校验人机决策恢复结构
        if not isinstance(decision, dict) or not isinstance(decision.get("decisions"), list):
            raise ValueError("Approval response must contain decisions")
        if (
            len(decision["decisions"]) != 1
            or not isinstance(decision["decisions"][0], dict)
            or decision["decisions"][0].get("type") not in {"approve", "reject"}
        ):
            raise ValueError("Expected exactly one approve or reject decision")

        # 若用户明确拒绝操作，构建错误 ToolMessage 返回模型
        if decision["decisions"][0]["type"] != "approve":
            return ToolMessage(
                content="Action rejected by user; the tool was not executed.",
                name=name,
                tool_call_id=request.tool_call["id"],
                status="error",
            )
        return None

    def wrap_tool_call(self, request: Any, handler: Any) -> Any:
        """同步拦截工具调用并执行人机审批流程。

        Args:
            request (Any): 工具调用请求上下文。
            handler (Any): 下一级工具执行器或下一个中间件包装函数。

        Returns:
            Any: 工具执行结果对象或拒绝消息。
        """
        rejected = self._approve(request)
        if rejected is not None:
            return rejected
        return handler(request)

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        """异步拦截工具调用并执行人机审批流程。

        Args:
            request (Any): 工具调用请求上下文。
            handler (Any): 下一级异步工具执行回调。

        Returns:
            Any: 异步工具执行结果对象或拒绝消息。
        """
        rejected = self._approve(request)
        if rejected is not None:
            return rejected
        return await handler(request)
