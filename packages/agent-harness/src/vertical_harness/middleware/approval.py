"""桌面人机协作审批中断中间件模块。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.types import interrupt


@dataclass(eq=False)
class DesktopApprovalMiddleware(AgentMiddleware):
    """在执行文件修改或外部命令等副作用工具前触发 LangGraph 人机审批中断的中间件。"""

    tools: list[Any] = field(default_factory=list, init=False)

    @staticmethod
    def _approve(request: Any) -> ToolMessage | None:
        """检查工具是否需要审批，需要时发起 interrupt 并在拒绝时返回错误 ToolMessage。"""
        name = request.tool.name
        if name not in {
            "write_file",
            "edit_file",
            "apply_patch",
            "delete",
            "execute",
            "execute_python_file",
            "shell",
            "write_todos",
        }:
            return None
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
        if not isinstance(decision, dict) or not isinstance(decision.get("decisions"), list):
            raise ValueError("Approval response must contain decisions")
        if (
            len(decision["decisions"]) != 1
            or not isinstance(decision["decisions"][0], dict)
            or decision["decisions"][0].get("type") not in {"approve", "reject"}
        ):
            raise ValueError("Expected exactly one approve or reject decision")
        if decision["decisions"][0]["type"] != "approve":
            return ToolMessage(
                content="Action rejected by user; the tool was not executed.",
                name=name,
                tool_call_id=request.tool_call["id"],
                status="error",
            )
        return None

    def wrap_tool_call(self, request: Any, handler: Any) -> Any:
        """同步拦截工具调用并执行人机审批流程。"""
        rejected = self._approve(request)
        if rejected is not None:
            return rejected
        return handler(request)

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        """异步拦截工具调用并执行人机审批流程。"""
        rejected = self._approve(request)
        if rejected is not None:
            return rejected
        return await handler(request)
