"""向后兼容的模型路由引擎门面模块。"""

from __future__ import annotations

import logging

from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_deepseek import ChatDeepSeek

from vertical_harness.config.settings import HarnessSettings
from vertical_harness.core.routing import ModelRouting

logger = logging.getLogger(__name__)


class EngineRouting(ModelRouting):
    """向后兼容的模型路由器门面。

    :author: tongliga
    :date: 2026-10-21
    """

    @staticmethod
    def init_configured_model(
        settings: HarnessSettings, model: str | None = None
    ) -> BaseChatModel:
        """基于已验证的配置项实例化 LangChain 聊天模型，支持动态猴子补丁。

        :param settings: 宿主配置对象。
        :type settings: vertical_harness.config.settings.HarnessSettings
        :param model: 可选的目标模型标识符。
        :type model: str | None
        :return: 实例化完成的 BaseChatModel 对象。
        :rtype: langchain_core.language_models.chat_models.BaseChatModel
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：解析目标模型名称并记录启动日志
        model_name = model or settings.model
        logger.debug("model_initialization_started", extra={"event_fields": {"model": model_name}})
        # 第二步：调用本模块的 init_chat_model 初始化模型，支持测试 mock 注入
        configured_model = init_chat_model(model_name, max_retries=5, **settings.model_init_kwargs)
        if isinstance(configured_model, ChatDeepSeek):
            configured_model.profile = {
                **(configured_model.profile or {}),
                "image_tool_message": False,
            }
        logger.debug(
            "model_initialization_completed", extra={"event_fields": {"model": model_name}}
        )
        return configured_model


init_configured_model = EngineRouting.init_configured_model
make_router = EngineRouting.make_router

__all__ = [
    "EngineRouting",
    "ModelRouting",
    "init_chat_model",
    "init_configured_model",
    "make_router",
]
