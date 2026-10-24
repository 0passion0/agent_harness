"""基于可信运行时上下文驱动的可选原生 LangChain 模型路由器模块。

本模块负责根据宿主配置初始化 LangChain 聊天模型客户端（`BaseChatModel`），
并在配置了双模型时，构造动态模型路由中间件 `ModelRouterMiddleware`。
支持 DeepSeek 等特定模型的协议兼容配置（如禁用多模态图片工具消息适配）。
"""

from __future__ import annotations

import logging

from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_deepseek import ChatDeepSeek

from vertical_harness.middleware.routing import ModelRouterMiddleware
from vertical_harness.models import HarnessSettings

# 模块级日志记录器，记录模型初始化与动态路由构建事件
logger = logging.getLogger(__name__)


class ModelRouting:
    """配置聊天模型并按可信运行时上下文创建可选动态路由器。

    提供工厂方法用于解析宿主配置并初始化底层 LangChain 聊天模型，
    统一禁用底层模型库自带的重试逻辑，交由上层 AgentMiddleware 统一调度。
    """

    @staticmethod
    def init_configured_model(settings: HarnessSettings, model: str | None = None) -> BaseChatModel:
        """基于已验证的 VA_* 配置项实例化 LangChain 聊天模型客户端。

        读取模型连接参数（API 密钥、Base URL），使用 `init_chat_model` 动态装载对应提供商的实现。
        将 `max_retries` 显式设为 0，防止底层提供商驱动与图层重试中间件叠加导致重试预算失控。
        针对 `ChatDeepSeek` 进行协议适配，设置 `image_tool_message=False` 以支持 JSON 序列化工具输出。

        Args:
            settings (HarnessSettings): 宿主全局配置对象，提供 API 密钥、端点及默认模型名。
            model (str | None): 可选的目标大模型标识符（如 `'deepseek:deepseek-chat'`）。
                若为 None，则默认使用 `settings.model`。

        Returns:
            BaseChatModel: 实例化并完成特定提供商适配的 LangChain 聊天模型实例。
        """
        model_name = model or settings.model
        logger.debug("model_initialization_started", extra={"event_fields": {"model": model_name}})

        # 模型重试由 AgentMiddleware 统一控制，避免 SDK 与图层叠加重试预算
        configured_model = init_chat_model(model_name, max_retries=0, **settings.model_init_kwargs)

        # 针对 DeepSeek 模型做特殊 profile 适配，使其兼容包含多模态二进制数据的工具消息序列化
        if isinstance(configured_model, ChatDeepSeek):
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
        """根据配置提供可选的动态模型路由中间件。

        若未成对配置 `economy_model` 和 `quality_model`，则返回 None，使用默认主模型；
        若均已配置，则分别实例化两个模型并封装为 `ModelRouterMiddleware`。

        Args:
            settings (HarnessSettings): 宿主配置实例，用于检查是否配置了双模型及模型名称。

        Returns:
            ModelRouterMiddleware | None: 若开启双模型路由则返回中间件实例，否则返回 None。
        """
        # 未配置双模型路由时直接跳过
        if not settings.has_dynamic_routing:
            logger.debug("model_routing_disabled")
            return None

        # 分别初始化经济型与质量型模型客户端
        economy_model = cls.init_configured_model(settings, settings.economy_model)
        quality_model = cls.init_configured_model(settings, settings.quality_model)
        logger.info("model_routing_enabled")
        return ModelRouterMiddleware(settings, economy_model, quality_model)
