"""上下文预算硬限制中间件，拦截并拒绝估算超出上限的模型请求。

本模块提供 `ContextLimitMiddleware` 中间件，作为模型调用前的硬性保护防线：
1. 模型 profile 容量感知：动态提取模型声明的 `max_input_tokens` 与宿主 `max_context_tokens` 比较，取二者较小值作为本次调用的硬上限；
2. 全量 Token 粗算：结合系统消息、历史对话与全部挂载工具 schema 计算总 Token 预算；
3. 超限防御拦截：若预算溢出则直接抛出 `ContextOverflowError`，坚决阻止超长上下文发送至模型导致 API 崩溃或高昂账单。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest
from langchain_core.exceptions import ContextOverflowError
from langchain_core.messages.utils import count_tokens_approximately


@dataclass(eq=False)
class ContextLimitMiddleware(AgentMiddleware):
    """拦截并拒绝上下文 Token 估算超出配置上限阈值的模型调用请求中间件。

    Attributes:
        maximum (int): 宿主配置的上下文 Token 预算上限。
        tools (list[Any]): 中间件挂载的工具列表（由基类协议要求，默认初始化为空列表）。
    """

    maximum: int
    tools: list[Any] = field(default_factory=list, init=False)

    @classmethod
    def input_limit(cls, request: ModelRequest[Any], maximum: int) -> int:
        """按本次请求实际使用的模型容量计算输入 Token 上限。

        Args:
            request (ModelRequest[Any]): 模型推理调用请求对象。
            maximum (int): 宿主配置的全局最大输入 Token 预算。

        Returns:
            int: 实际生效的输入 Token 上限（模型 profile 容量与全局上限的较小值）。
        """
        profile = getattr(request.model, "profile", None)
        limit = profile.get("max_input_tokens") if isinstance(profile, dict) else None
        if isinstance(limit, int) and not isinstance(limit, bool) and limit > 0:
            return min(limit, maximum)
        return maximum

    def _check(self, request: ModelRequest[Any]) -> None:
        """估算请求包含的所有消息与工具描述 Token 总量，超限则抛出异常。

        组合系统消息与会话消息列表，使用 `count_tokens_approximately` 快速估算，并在超限时抛出中断。

        Args:
            request (ModelRequest[Any]): 待审计的模型请求对象。

        Raises:
            ContextOverflowError: 当估算的 Token 总数超过输入上限时抛出。
        """
        messages = request.messages
        if request.system_message is not None:
            messages = [request.system_message, *messages]
        estimate = count_tokens_approximately(messages, tools=request.tools)
        limit = self.input_limit(request, self.maximum)
        if estimate > limit:
            raise ContextOverflowError(
                f"Estimated conversation history ({estimate} tokens) exceeds configured "
                f"max_context_tokens ({limit}); history was not sent to the model"
            )

    def wrap_model_call(self, request: ModelRequest[Any], handler: Any) -> Any:
        """同步拦截模型调用请求并执行容量检查。

        Args:
            request (ModelRequest[Any]): 模型调用请求对象。
            handler (Any): 下一级模型执行处理器。

        Returns:
            Any: 模型调用结果响应。
        """
        self._check(request)
        return handler(request)

    async def awrap_model_call(self, request: ModelRequest[Any], handler: Any) -> Any:
        """异步拦截模型调用请求并执行容量检查。

        Args:
            request (ModelRequest[Any]): 待异步执行的模型调用请求对象。
            handler (Any): 下一级异步执行器。

        Returns:
            Any: 异步模型调用的响应结果。
        """
        self._check(request)
        return await handler(request)
