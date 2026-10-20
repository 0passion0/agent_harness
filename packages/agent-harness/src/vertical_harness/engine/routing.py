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
from langchain_deepseek import ChatDeepSeek

from vertical_harness.config.settings import HarnessSettings, TaskTier

logger = logging.getLogger(__name__)


def init_configured_model(
    settings: HarnessSettings,
    model: str | None = None,
) -> BaseChatModel:
    """基于已验证的 VA_* 配置项实例化 LangChain 聊天模型。

    :param settings: 包含模型名称、API 密钥与 Base URL 的宿主配置对象。
    :type settings: vertical_harness.config.settings.HarnessSettings
    :param model: 可选的目标模型标识符字符串，未指定时默认使用 settings.model。
    :type model: str | None
    :return: 实例化完成的 LangChain 基础聊天模型（BaseChatModel）对象。
    :rtype: langchain_core.language_models.chat_models.BaseChatModel
    :author: tongliga
    :date: 2026-10-19
    """
    # 第一步：解析目标模型名称并记录启动日志
    model_name = model or settings.model
    logger.debug("model_initialization_started", extra={"event_fields": {"model": model_name}})
    # 第二步：调用 init_chat_model 初始化模型，设置 5 次退避重试并注入连接参数
    configured_model = init_chat_model(model_name, max_retries=5, **settings.model_init_kwargs)
    if isinstance(configured_model, ChatDeepSeek):
        # The adapter JSON-serializes tool content lists, including binary payloads.
        configured_model.profile = {
            **(configured_model.profile or {}),
            "image_tool_message": False,
        }
    logger.debug("model_initialization_completed", extra={"event_fields": {"model": model_name}})
    return configured_model


def make_router(settings: HarnessSettings) -> AgentMiddleware[Any, Any, Any] | None:
    """提供可选择性启用的模型动态路由中间件，避免重写底层模型调用循环。

    :param settings: 包含模型标识符及路由相关配置的对象。
    :type settings: vertical_harness.config.settings.HarnessSettings
    :return: LangChain 包装后的模型调用路由中间件，未配置路由时返回 None。
    :rtype: langchain.agents.middleware.AgentMiddleware[typing.Any, typing.Any, typing.Any] | None
    :raises ValueError: 当仅配置了经济模型或品质模型之一时抛出（两者必须同时配置）。
    :author: tongliga
    :date: 2026-10-19
    """
    # 第一步：检查是否开启双模型动态路由，未开启则短路返回 None
    if not settings.has_dynamic_routing:
        logger.debug("model_routing_disabled")
        return None

    # 第二步：分别预热初始化经济模型与品质模型实例
    economy_model = init_configured_model(settings, settings.economy_model)
    quality_model = init_configured_model(settings, settings.quality_model)
    logger.info("model_routing_enabled")

    def select_model(request: ModelRequest) -> ModelRequest:
        """根据每次运行由宿主注入并验证的可信任务层级动态选择底层模型。

        :param request: 包含运行时上下文及调用参数的模型调用请求对象。
        :type request: langchain.agents.middleware.ModelRequest
        :return: 已选择底层模型的请求对象。
        :rtype: langchain.agents.middleware.ModelRequest
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：从当前请求的运行时上下文读取 task_tier
        task_tier = getattr(request.runtime.context, "task_tier", TaskTier.QUALITY)
        # 第二步：匹配对应梯队模型并记录分流日志
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
        # 第三步：覆盖请求绑定的底层模型并返回
        return request.override(model=target_model)

    class ModelRouterMiddleware(AgentMiddleware[Any, Any, Any]):
        """在原生同步与异步模型调用路径间统一共享动态模型选择逻辑。"""

        def wrap_model_call(
            self,
            request: ModelRequest,
            handler: Callable[[ModelRequest], ModelResponse],
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
            return handler(select_model(request))

        async def awrap_model_call(
            self,
            request: ModelRequest,
            handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
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
            return await handler(select_model(request))

    # 第三步：实例化并返回模型路由中间件对象
    return ModelRouterMiddleware()


__all__ = [
    "init_configured_model",
    "make_router",
]
