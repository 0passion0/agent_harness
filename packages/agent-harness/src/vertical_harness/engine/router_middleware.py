"""原生动态模型路由中间件。"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse

from vertical_harness.config.settings import HarnessSettings, TaskTier

logger = logging.getLogger(__name__)


@dataclass(eq=False)
class ModelRouterMiddleware(AgentMiddleware[Any, Any, Any]):
    """在原生同步与异步模型调用路径间统一共享动态模型选择逻辑。"""

    settings: HarnessSettings
    economy_model: Any
    quality_model: Any

    def wrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]
    ) -> ModelResponse:
        """拦截并包装同步模型调用请求，动态覆盖为目标路由模型。

        :param request: 原始模型请求对象。
        :type request: langchain.agents.middleware.ModelRequest
        :param handler: 下游调用处理器函数。
        :type handler: collections.abc.Callable[[langchain.agents.middleware.ModelRequest], langchain.agents.middleware.ModelResponse]
        :return: 模型调用响应对象。
        :rtype: langchain.agents.middleware.ModelResponse
        :author: tongliga
        :date: 2026-10-19
        """
        logger.debug("wrapping_model_call_sync")
        return handler(self.select_model(request))

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]]
    ) -> ModelResponse:
        """拦截并包装异步模型调用请求，动态覆盖为目标路由模型。

        :param request: 原始模型请求对象。
        :type request: langchain.agents.middleware.ModelRequest
        :param handler: 下游异步调用处理器函数。
        :type handler: collections.abc.Callable[[langchain.agents.middleware.ModelRequest], collections.abc.Awaitable[langchain.agents.middleware.ModelResponse]]
        :return: 模型调用响应对象。
        :rtype: langchain.agents.middleware.ModelResponse
        :author: tongliga
        :date: 2026-10-19
        """
        logger.debug("wrapping_model_call_async")
        return await handler(self.select_model(request))

    def select_model(self, request: ModelRequest) -> ModelRequest:
        """根据宿主注入并验证的可信任务层级动态选择底层模型。

        :param request: 包含运行时上下文及调用参数的模型调用请求对象。
        :type request: langchain.agents.middleware.ModelRequest
        :return: 已选择底层模型的请求对象。
        :rtype: langchain.agents.middleware.ModelRequest
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：从当前请求的运行时上下文读取 task_tier
        task_tier = getattr(request.runtime.context, "task_tier", TaskTier.QUALITY)
        economy = task_tier == TaskTier.ECONOMY
        # 第二步：匹配对应梯队模型并记录分流日志
        logger.debug(
            "model_routed",
            extra={
                "event_fields": {
                    "task_tier": TaskTier.ECONOMY.value if economy else TaskTier.QUALITY.value,
                    "model": self.settings.economy_model if economy else self.settings.quality_model,
                }
            },
        )
        # 第三步：覆盖请求绑定的底层模型并返回
        return request.override(model=self.economy_model if economy else self.quality_model)
