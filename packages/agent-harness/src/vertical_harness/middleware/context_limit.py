"""上下文预算硬限制中间件，拦截并拒绝估算超出上限的模型请求。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest
from langchain_core.exceptions import ContextOverflowError
from langchain_core.messages.utils import count_tokens_approximately


@dataclass(eq=False)
class ContextLimitMiddleware(AgentMiddleware):
    """拦截并拒绝上下文 Token 估算超出配置上限阈值的模型调用请求中间件。"""

    maximum: int
    tools: list[Any] = field(default_factory=list, init=False)

    @classmethod
    def input_limit(cls, request: ModelRequest[Any], maximum: int) -> int:
        """按本次请求实际使用的模型容量计算输入 Token 上限。"""
        profile = getattr(request.model, "profile", None)
        limit = profile.get("max_input_tokens") if isinstance(profile, dict) else None
        if isinstance(limit, int) and not isinstance(limit, bool) and limit > 0:
            return min(limit, maximum)
        return maximum

    def _check(self, request: ModelRequest[Any]) -> None:
        """估算请求包含的所有消息与工具描述 Token 总量，超限则抛出异常。"""
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
        """同步拦截模型调用请求并执行容量检查。"""
        self._check(request)
        return handler(request)

    async def awrap_model_call(self, request: ModelRequest[Any], handler: Any) -> Any:
        """异步拦截模型调用请求并执行容量检查。"""
        self._check(request)
        return await handler(request)
