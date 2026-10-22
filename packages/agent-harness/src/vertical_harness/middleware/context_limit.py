"""上下文预算硬限制中间件，拦截并拒绝估算超出上限的模型请求。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest
from langchain_core.messages.utils import count_tokens_approximately


@dataclass(eq=False)
class ContextLimitMiddleware(AgentMiddleware):
    """拦截并拒绝上下文 Token 估算超出配置上限阈值的模型调用请求中间件。

    :author: tongliga
    :date: 2026-10-21
    """

    maximum: int
    tools: list[Any] = field(default_factory=list, init=False)

    def _check(self, request: ModelRequest[Any]) -> None:
        """估算请求包含的所有消息与工具描述 Token 总量，超限则抛出异常。

        :param request: 模型请求对象。
        :type request: langchain.agents.middleware.ModelRequest[typing.Any]
        :return: 无返回值。
        :rtype: None
        :raises ValueError: 当 Token 估算值超出配置的 maximum 阈值时抛出。
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：合并系统提示词与历史消息列表
        messages = request.messages
        if request.system_message is not None:
            messages = [request.system_message, *messages]

        # 第二步：调用 LangChain 工具函数近似计算 Token 数量
        estimate = count_tokens_approximately(messages, tools=request.tools)

        # 第三步：超限断言拦截，防止请求发送至模型服务商
        if estimate > self.maximum:
            raise ValueError(
                f"Estimated conversation history ({estimate} tokens) exceeds configured "
                f"max_context_tokens ({self.maximum}); history was not sent to the model"
            )

    def wrap_model_call(self, request: ModelRequest[Any], handler: Any) -> Any:
        """同步拦截模型调用请求并执行容量检查。

        :param request: 模型请求对象。
        :type request: langchain.agents.middleware.ModelRequest[typing.Any]
        :param handler: 下游调用处理器函数。
        :type handler: typing.Any
        :return: 模型响应结果。
        :rtype: typing.Any
        :author: tongliga
        :date: 2026-10-21
        """
        self._check(request)
        return handler(request)

    async def awrap_model_call(self, request: ModelRequest[Any], handler: Any) -> Any:
        """异步拦截模型调用请求并执行容量检查。

        :param request: 模型请求对象。
        :type request: langchain.agents.middleware.ModelRequest[typing.Any]
        :param handler: 下游异步调用处理器函数。
        :type handler: typing.Any
        :return: 模型响应结果。
        :rtype: typing.Any
        :author: tongliga
        :date: 2026-10-21
        """
        self._check(request)
        return await handler(request)
