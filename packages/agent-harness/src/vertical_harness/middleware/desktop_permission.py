from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from langchain.agents.middleware import AgentMiddleware


@dataclass(eq=False)
class DesktopPermissionMiddleware(AgentMiddleware):
    """在只读模式（read_only）下强制禁用所有具副作用工具的权限中间件。

    :author: tongliga
    :date: 2026-10-21
    """

    _READ_ONLY_TOOLS = frozenset(
        {"read_file", "ls", "glob", "grep", "ask_user", "web_search", "write_todos"}
    )

    mode: str
    tools: list[Any] = field(default_factory=list, init=False)

    def wrap_tool_call(self, request: Any, handler: Any) -> Any:
        """同步拦截工具调用，若在只读模式下尝试执行白名单外工具则抛出权限错误。

        :param request: 工具调用请求对象。
        :type request: typing.Any
        :param handler: 下游工具调用处理器。
        :type handler: typing.Any
        :return: 工具调用结果。
        :rtype: typing.Any
        :raises PermissionError: 当只读模式下调用非只读工具时抛出。
        :author: tongliga
        :date: 2026-10-21
        """
        if self.mode == "read_only" and request.tool.name not in self._READ_ONLY_TOOLS:
            raise PermissionError(f"Tool {request.tool.name} is disabled in read_only mode")
        return handler(request)

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        """异步拦截工具调用，若在只读模式下尝试执行白名单外工具则抛出权限错误。

        :param request: 工具调用请求对象。
        :type request: typing.Any
        :param handler: 下游异步工具调用处理器。
        :type handler: typing.Any
        :return: 工具调用结果。
        :rtype: typing.Any
        :raises PermissionError: 当只读模式下调用非只读工具时抛出。
        :author: tongliga
        :date: 2026-10-21
        """
        if self.mode == "read_only" and request.tool.name not in self._READ_ONLY_TOOLS:
            raise PermissionError(f"Tool {request.tool.name} is disabled in read_only mode")
        return await handler(request)
