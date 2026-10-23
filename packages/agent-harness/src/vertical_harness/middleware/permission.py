"""桌面只读权限受控执行中间件模块。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest


@dataclass(eq=False)
class DesktopPermissionMiddleware(AgentMiddleware):
    """在只读模式（read_only）下强制禁用所有具副作用工具的权限中间件。"""

    _READ_ONLY_TOOLS = frozenset(
        {"read_file", "ls", "glob", "grep", "ask_user", "web_search", "write_todos"}
    )

    mode: str
    tools: list[Any] = field(default_factory=list, init=False)

    def _prepare(self, request: ModelRequest) -> ModelRequest:
        """只向模型公开当前权限允许的工具，执行入口仍独立强制校验。"""
        if self.mode != "read_only":
            return request
        return request.override(
            tools=[
                tool
                for tool in request.tools
                if (tool.get("function", tool).get("name") if isinstance(tool, dict) else tool.name)
                in self._READ_ONLY_TOOLS
            ]
        )

    def wrap_model_call(self, request: ModelRequest, handler: Any) -> Any:
        """同步过滤工具描述。"""
        return handler(self._prepare(request))

    async def awrap_model_call(self, request: ModelRequest, handler: Any) -> Any:
        """异步过滤工具描述。"""
        return await handler(self._prepare(request))

    def wrap_tool_call(self, request: Any, handler: Any) -> Any:
        """同步拦截工具调用，若在只读模式下尝试执行白名单外工具则抛出权限错误。"""
        if self.mode == "read_only" and request.tool.name not in self._READ_ONLY_TOOLS:
            raise PermissionError(f"Tool {request.tool.name} is disabled in read_only mode")
        return handler(request)

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        """异步拦截工具调用，若在只读模式下尝试执行白名单外工具则抛出权限错误。"""
        return await self.wrap_tool_call(request, handler)
