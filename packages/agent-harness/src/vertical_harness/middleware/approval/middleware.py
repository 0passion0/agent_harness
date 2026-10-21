"""桌面人机协作审批中断中间件模块。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.types import interrupt


@dataclass(eq=False)
class DesktopApprovalMiddleware(AgentMiddleware):
    """在执行文件修改或外部命令等副作用工具前触发 LangGraph 人机审批中断的中间件。

    :author: tongliga
    :date: 2026-10-21
    """

    tools: list[Any] = field(default_factory=list, init=False)

    @staticmethod
    def _approve(request: Any) -> ToolMessage | None:
        """检查工具是否需要审批，需要时发起 interrupt 并在拒绝时返回错误 ToolMessage。

        :param request: 工具请求对象。
        :type request: typing.Any
        :return: 若用户拒绝则返回包含拒绝说明的 ToolMessage；若同意或无需审批则返回 None。
        :rtype: langchain_core.messages.ToolMessage | None
        :raises ValueError: 当审批应答格式不合法时抛出。
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：判断工具是否属于需要审批的高风险操作集
        name = request.tool.name
        if name not in {
            "write_file",
            "edit_file",
            "delete",
            "execute",
            "execute_python_file",
            "write_todos",
        }:
            return None

        # 第二步：挂起当前执行流，向桌面端发出 approval 审批请求
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

        # 第三步：校验审批响应结构必须为单个 approve 或 reject 决策
        if not isinstance(decision, dict) or not isinstance(decision.get("decisions"), list):
            raise ValueError("Approval response must contain decisions")
        if (
            len(decision["decisions"]) != 1
            or not isinstance(decision["decisions"][0], dict)
            or decision["decisions"][0].get("type") not in {"approve", "reject"}
        ):
            raise ValueError("Expected exactly one approve or reject decision")

        # 第四步：若用户拒绝，则生成对应的错误状态 ToolMessage 中断执行
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

        :param request: 工具请求对象。
        :type request: typing.Any
        :param handler: 下游工具调用处理器。
        :type handler: typing.Any
        :return: 工具调用结果或被拒绝时的 ToolMessage。
        :rtype: typing.Any
        :author: tongliga
        :date: 2026-10-21
        """
        rejected = self._approve(request)
        if rejected is not None:
            return rejected
        return handler(request)

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        """异步拦截工具调用并执行人机审批流程。

        :param request: 工具请求对象。
        :type request: typing.Any
        :param handler: 下游异步工具调用处理器。
        :type handler: typing.Any
        :return: 工具调用结果或被拒绝时的 ToolMessage。
        :rtype: typing.Any
        :author: tongliga
        :date: 2026-10-21
        """
        rejected = self._approve(request)
        if rejected is not None:
            return rejected
        return await handler(request)


__all__ = ["DesktopApprovalMiddleware"]
