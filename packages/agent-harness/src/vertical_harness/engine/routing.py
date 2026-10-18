"""基于可信运行时上下文驱动的可选原生 LangChain 模型路由器模块。"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel

from vertical_harness.config.settings import HarnessSettings, TaskTier

logger = logging.getLogger(__name__)


def init_configured_model(
    settings: HarnessSettings,
    model: str | None = None,
) -> BaseChatModel:
    """基于已验证的 VA_* 配置项实例化 LangChain 聊天模型。

    :param settings: 包含模型名称、API 密钥与 Base URL 的宿主配置对象。
    :param model: 可选的目标模型标识符字符串，未指定时默认使用 settings.model。
    :return: 实例化完成的 LangChain 基础聊天模型（BaseChatModel）对象。
    """
    model_name = model or settings.model
    logger.debug("model_initialization_started", extra={"event_fields": {"model": model_name}})
    # Reuse provider backoff without wrapping tool binding or stacking retry loops.
    configured_model = init_chat_model(model_name, max_retries=5, **settings.model_init_kwargs)
    logger.debug("model_initialization_completed", extra={"event_fields": {"model": model_name}})
    return configured_model


def make_router(settings: HarnessSettings) -> AgentMiddleware[Any, Any, Any] | None:
    """提供可选择性启用的模型动态路由中间件，避免重写底层模型调用循环。

    :param settings: 包含模型标识符及路由相关配置的对象。
    :return: LangChain 包装后的模型调用路由中间件，未配置路由时返回 None。
    :raises ValueError: 当仅配置了经济模型或品质模型之一时抛出（两者必须同时配置）。
    """
    if not settings.has_dynamic_routing:
        logger.debug("model_routing_disabled")
        return None

    economy_model = init_configured_model(settings, settings.economy_model)
    quality_model = init_configured_model(settings, settings.quality_model)
    logger.info("model_routing_enabled")

    def select_model(request: ModelRequest) -> ModelRequest:
        """根据每次运行由宿主注入并验证的可信任务层级动态选择底层模型。

        :param request: 包含运行时上下文及调用参数的模型调用请求对象。
        :return: 已选择底层模型的请求对象。
        """
        task_tier = getattr(request.runtime.context, "task_tier", TaskTier.QUALITY)
        target_model = economy_model if task_tier == TaskTier.ECONOMY else quality_model
        logger.debug(
            "model_routed",
            extra={
                "event_fields": {
                    "task_tier": TaskTier.ECONOMY.value
                    if task_tier == TaskTier.ECONOMY
                    else TaskTier.QUALITY.value,
                    "model": settings.economy_model
                    if task_tier == TaskTier.ECONOMY
                    else settings.quality_model,
                }
            },
        )
        return request.override(model=target_model)

    class ModelRouterMiddleware(AgentMiddleware[Any, Any, Any]):
        """Share model selection across native sync and async invocation paths."""

        def wrap_model_call(
            self,
            request: ModelRequest,
            handler: Callable[[ModelRequest], ModelResponse],
        ) -> ModelResponse:
            return handler(select_model(request))

        async def awrap_model_call(
            self,
            request: ModelRequest,
            handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
        ) -> ModelResponse:
            return await handler(select_model(request))

    return ModelRouterMiddleware()


__all__ = [
    "init_configured_model",
    "make_router",
]
