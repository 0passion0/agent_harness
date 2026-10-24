"""原生动态模型路由中间件模块。

本模块提供 `ModelRouterMiddleware` 中间件，负责在多模型混合部署场景下实现动态按需路由：
根据宿主注入的可信任务分级 `request.runtime.context.task_tier`（economy 或 quality），
在每次调用模型前动态覆写 `request.model` 为对应的经济型或旗舰质量型聊天模型实例。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse

from vertical_harness.config.settings import HarnessSettings
from vertical_harness.config.task_tier import TaskTier

# 模块级日志记录器，记录动态路由决策事件
logger = logging.getLogger(__name__)


@dataclass(eq=False)
class ModelRouterMiddleware(AgentMiddleware[Any, Any, Any]):
    """在原生同步与异步模型调用路径间统一共享动态模型选择逻辑。

    Attributes:
        settings (HarnessSettings): 宿主全局配置对象，提供模型名称元数据。
        economy_model (Any): 实例化好的经济型聊天模型（用于简单处理或高吞吐任务）。
        quality_model (Any): 实例化好的高品质旗舰聊天模型（用于深度逻辑推理）。
    """

    settings: HarnessSettings
    economy_model: Any
    quality_model: Any

    def wrap_model_call(
        self, request: ModelRequest[Any], handler: Callable[[ModelRequest[Any]], ModelResponse]
    ) -> ModelResponse:
        """拦截并包装同步模型调用请求，动态覆盖为目标路由模型。

        Args:
            request (ModelRequest[Any]): 模型调用请求对象。
            handler (Callable[[ModelRequest[Any]], ModelResponse]): 下一级模型调用处理器。

        Returns:
            ModelResponse: 目标模型执行返回的响应对象。
        """
        logger.debug("wrapping_model_call_sync")
        return handler(self.prepare(request))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        """拦截并包装异步模型调用请求，动态覆盖为目标路由模型。

        Args:
            request (ModelRequest[Any]): 异步模型调用请求对象。
            handler (Callable[[ModelRequest[Any]], Awaitable[ModelResponse]]): 下一级异步执行器。

        Returns:
            ModelResponse: 目标模型执行返回的响应对象。
        """
        logger.debug("wrapping_model_call_async")
        return await handler(await self.aprepare(request))

    async def aprepare(self, request: ModelRequest[Any]) -> ModelRequest[Any]:
        """异步准备模型请求，保持与同步 prepare 一致的模型动态路由策略。

        Args:
            request (ModelRequest[Any]): 待准备的请求实例。

        Returns:
            ModelRequest[Any]: 替换模型实例后的新请求对象。
        """
        return self.prepare(request)

    def prepare(self, request: ModelRequest[Any]) -> ModelRequest[Any]:
        """根据宿主注入并验证的可信任务层级动态选择底层模型。

        从 `request.runtime.context` 中读取 `task_tier`，若为 `TaskTier.ECONOMY` 则路由至经济型模型，
        否则默认使用高质量旗舰模型。

        Args:
            request (ModelRequest[Any]): 原始模型调用请求。

        Returns:
            ModelRequest[Any]: 覆盖了 `model` 属性的目标模型请求。
        """
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
