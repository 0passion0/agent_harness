"""原生动态模型路由中间件模块。"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse

from vertical_harness.config.settings import HarnessSettings
from vertical_harness.config.task_tier import TaskTier

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
        """拦截并包装同步模型调用请求，动态覆盖为目标路由模型。"""
        logger.debug("wrapping_model_call_sync")
        return handler(self.prepare(request))

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]]
    ) -> ModelResponse:
        """拦截并包装异步模型调用请求，动态覆盖为目标路由模型。"""
        logger.debug("wrapping_model_call_async")
        return await handler(await self.aprepare(request))

    async def aprepare(self, request: ModelRequest) -> ModelRequest:
        return self.prepare(request)

    def prepare(self, request: ModelRequest) -> ModelRequest:
        """根据宿主注入并验证的可信任务层级动态选择底层模型。"""
        task_tier = getattr(request.runtime.context, "task_tier", TaskTier.QUALITY)
        economy = task_tier == TaskTier.ECONOMY
        logger.debug(
            "model_routed",
            extra={
                "event_fields": {
                    "task_tier": TaskTier.ECONOMY.value if economy else TaskTier.QUALITY.value,
                    "model": self.settings.economy_model
                    if economy
                    else self.settings.quality_model,
                }
            },
        )
        return request.override(model=self.economy_model if economy else self.quality_model)
