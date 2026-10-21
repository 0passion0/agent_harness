"""基于可信运行时上下文驱动的可选原生 LangChain 模型路由器模块。"""

from __future__ import annotations

import logging

from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_deepseek import ChatDeepSeek

from vertical_harness.config.settings import HarnessSettings
from vertical_harness.engine.router_middleware import ModelRouterMiddleware

logger = logging.getLogger(__name__)


class ModelRouting:
    """配置聊天模型并按可信运行时上下文创建可选动态路由器。"""

    @staticmethod
    def init_configured_model(
        settings: HarnessSettings, model: str | None = None
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
        logger.debug(
            "model_initialization_completed", extra={"event_fields": {"model": model_name}}
        )
        return configured_model

    @classmethod
    def make_router(cls, settings: HarnessSettings) -> ModelRouterMiddleware | None:
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
        economy_model = cls.init_configured_model(settings, settings.economy_model)
        quality_model = cls.init_configured_model(settings, settings.quality_model)
        logger.info("model_routing_enabled")
        # 第三步：实例化并返回模型路由中间件对象
        return ModelRouterMiddleware(settings, economy_model, quality_model)

init_configured_model = ModelRouting.init_configured_model
make_router = ModelRouting.make_router

__all__ = ["ModelRouting", "init_configured_model", "make_router"]
