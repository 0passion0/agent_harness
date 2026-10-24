"""桌面只读权限受控执行中间件模块。

本模块提供 `DesktopPermissionMiddleware` 中间件，实现权限沙箱防护：
1. 工具暴露过滤：当权限模式为 `read_only` 时，在向模型发起请求前，从挂载工具列表中物理剔除所有写、编辑、命令等有副作用的工具，
   仅公开经过审查的只读工具白名单；
2. 工具调用拦截验证：若模型试图越权调用已隐藏的具副作用工具，在执行调用层直接抛出 `PermissionError` 拒绝执行。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar

from langchain.agents.middleware import AgentMiddleware, ModelRequest


@dataclass(eq=False)
class DesktopPermissionMiddleware(AgentMiddleware):
    """在只读模式（read_only）下强制禁用所有具副作用工具的权限中间件。

    Attributes:
        mode (str): 当前运行模式，支持 `"read_only"`（严格只读）、`"ask"`（人机审批）、`"full"`（完全授权）。
        tools (list[Any]): 挂载的工具列表（由基类协议要求，默认初始化为空列表）。
    """

    # 只读模式下允许安全暴露和调用的无副作用只读工具白名单集合
    _READ_ONLY_TOOLS: ClassVar[frozenset[str]] = frozenset(
        {"read_file", "ls", "glob", "grep", "ask_user", "web_search", "write_todos"}
    )

    mode: str
    tools: list[Any] = field(default_factory=list, init=False)

    def _prepare(self, request: ModelRequest[Any]) -> ModelRequest[Any]:
        """只向模型公开当前权限允许的工具，执行入口仍独立强制校验。

        Args:
            request (ModelRequest[Any]): 模型调用请求对象。

        Returns:
            ModelRequest[Any]: 过滤后的模型请求实例（若处于 read_only 模式则仅包含白名单只读工具）。
        """
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

    def wrap_model_call(self, request: ModelRequest[Any], handler: Any) -> Any:
        """同步过滤工具描述。

        Args:
            request (ModelRequest[Any]): 模型请求。
            handler (Any): 下一级模型执行处理器。

        Returns:
            Any: 模型调用结果。
        """
        return handler(self._prepare(request))

    async def awrap_model_call(self, request: ModelRequest[Any], handler: Any) -> Any:
        """异步过滤工具描述。

        Args:
            request (ModelRequest[Any]): 异步模型请求。
            handler (Any): 下一级异步执行器。

        Returns:
            Any: 异步模型调用结果。
        """
        return await handler(self._prepare(request))

    def wrap_tool_call(self, request: Any, handler: Any) -> Any:
        """同步拦截工具调用，若在只读模式下尝试执行白名单外工具则抛出权限错误。

        Args:
            request (Any): 工具调用请求实体。
            handler (Any): 下一级工具调用执行器。

        Returns:
            Any: 工具执行结果。

        Raises:
            PermissionError: 当处于只读模式且请求的工具不属于只读白名单时抛出。
        """
        if self.mode == "read_only" and request.tool.name not in self._READ_ONLY_TOOLS:
            raise PermissionError(f"Tool {request.tool.name} is disabled in read_only mode")
        return handler(request)

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        """异步拦截工具调用，若在只读模式下尝试执行白名单外工具则抛出权限错误。

        Args:
            request (Any): 工具调用请求实体。
            handler (Any): 下一级异步工具处理器。

        Returns:
            Any: 异步工具执行结果。

        Raises:
            PermissionError: 当处于只读模式且请求的工具不属于只读白名单时抛出。
        """
        return await self.wrap_tool_call(request, handler)
