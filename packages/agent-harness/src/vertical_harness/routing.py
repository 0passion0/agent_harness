"""基于可信运行时上下文驱动的可选原生 LangChain 模型路由器模块。"""

from collections.abc import Callable
from typing import Any

from langchain.agents.middleware import ModelRequest, ModelResponse, wrap_model_call
from langchain.chat_models import init_chat_model

from vertical_harness.settings import HarnessSettings


def init_configured_model(settings: HarnessSettings, model: str | None = None):
    """基于已验证的 VA_* 配置项实例化 LangChain 聊天模型。

    :param settings: 包含模型名称、API 密钥与 Base URL 的宿主配置对象。
    :param model: 可选的目标模型标识符字符串，未指定时默认使用 settings.model。
    :return: 实例化完成的 LangChain 基础聊天模型（BaseChatModel）对象。
    """
    # 构造传递给 init_chat_model 的附加参数字典
    kwargs: dict[str, Any] = {}
    # 若配置了 API 密钥，安全解包 SecretStr 明文
    if settings.api_key is not None:
        kwargs["api_key"] = settings.api_key.get_secret_value()
    # 若配置了自定义 Base URL，传递给模型初始化接口
    if settings.base_url is not None:
        kwargs["base_url"] = settings.base_url
    # 根据指定的模型名称（或默认配置模型）完成模型实例初始化
    return init_chat_model(model or settings.model, **kwargs)


def make_router(settings: HarnessSettings):
    """提供可选择性启用的模型动态路由中间件，避免重写底层模型调用循环。

    :param settings: 包含模型标识符及路由相关配置的对象。
    :return: LangChain 包装后的模型调用路由中间件，未配置路由时返回 None。
    :raises ValueError: 当仅配置了经济模型或品质模型之一时抛出（两者必须同时配置）。
    """
    # 若经济模型与高品质模型均未设置，则不启用动态路由
    if not settings.economy_model and not settings.quality_model:
        return None
    # 若仅配置了其中一个，则说明配置不完整，抛出异常阻断启动
    if not settings.economy_model or not settings.quality_model:
        raise ValueError("Both VA_ECONOMY_MODEL and VA_QUALITY_MODEL must be set")

    # 分别预先初始化经济型模型与高品质模型实例
    economy = init_configured_model(settings, settings.economy_model)
    quality = init_configured_model(settings, settings.quality_model)

    @wrap_model_call
    def route_model(
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        """根据每次运行由宿主注入并验证的可信任务层级动态选择底层模型。

        :param request: 包含运行时上下文及调用参数的模型调用请求对象。
        :param handler: 后续的模型调用处理器函数。
        :return: 模型调用生成的响应对象。
        """
        # 从运行时上下文中安全获取当前任务层级 task_tier，默认回退为 quality
        task_tier = getattr(request.runtime.context, "task_tier", "quality")
        # 经济型任务路由至 economy 模型，其余路由至 quality 模型
        model = economy if task_tier == "economy" else quality
        # 覆盖请求中的模型实例并传递给下游处理器执行
        return handler(request.override(model=model))

    return route_model

