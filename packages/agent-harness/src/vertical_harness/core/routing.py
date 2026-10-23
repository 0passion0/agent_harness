"""基于可信运行时上下文驱动的可选原生 LangChain 模型路由器模块。"""

from __future__ import annotations

import logging

from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_deepseek import ChatDeepSeek

from vertical_harness.config.settings import HarnessSettings
from vertical_harness.middleware.routing import ModelRouterMiddleware

logger = logging.getLogger(__name__)


class ModelRouting:
    """配置聊天模型并按可信运行时上下文创建可选动态路由器。"""

    @staticmethod
    def init_configured_model(settings: HarnessSettings, model: str | None = None) -> BaseChatModel:
        """基于已验证的 VA_* 配置项实例化 LangChain 聊天模型。"""
        model_name = model or settings.model
        logger.debug("model_initialization_started", extra={"event_fields": {"model": model_name}})
        # 模型重试由 AgentMiddleware 统一控制，避免 SDK 与图层叠加重试预算。
        configured_model = init_chat_model(model_name, max_retries=0, **settings.model_init_kwargs)
        if isinstance(configured_model, ChatDeepSeek):
            # 适配器 JSON 序列化工具内容列表（含二进制载荷）
            configured_model.profile = {
                **(configured_model.profile or {}),
                "image_tool_message": False,
            }
        logger.debug(
            "model_initialization_completed", extra={"event_fields": {"model": model_name}}
        )
        return configured_model

    @classmethod
    def make_router(cls, settings: HarnessSettings) -> ModelRouterMiddleware | None:
        """提供可选择性启用的模型动态路由中间件，避免重写底层模型调用循环。"""
        if not settings.has_dynamic_routing:
            logger.debug("model_routing_disabled")
            return None
        economy_model = cls.init_configured_model(settings, settings.economy_model)
        quality_model = cls.init_configured_model(settings, settings.quality_model)
        logger.info("model_routing_enabled")
        return ModelRouterMiddleware(settings, economy_model, quality_model)
