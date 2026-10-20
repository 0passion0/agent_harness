"""LangSmith 连接和 tracing 生命周期；模型连接始终由 VA_* 配置管理。"""

import logging
from collections.abc import Iterator
from contextlib import closing, contextmanager

from langsmith import Client, tracing_context
from langsmith.run_helpers import get_tracing_context
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class LangSmithSettings(BaseSettings):
    """显式读取 .env，避免仅由 SDK 读取进程环境而漏掉 tracing 配置。"""

    model_config = SettingsConfigDict(env_prefix="LANGSMITH_", env_file=".env", extra="ignore")

    tracing: bool = False
    api_key: SecretStr | None = None
    endpoint: str | None = None
    project: str = "vertical-research-agent"

    def create_client(self, *, auto_batch_tracing: bool = True) -> Client:
        """基于已加载的凭据与端点创建 LangSmith 客户端实例。

        :param auto_batch_tracing: 是否在后台自动批量异步上报 Trace 数据。
        :type auto_batch_tracing: bool
        :return: 初始化完成的 LangSmith Client 实例。
        :rtype: langsmith.Client
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：记录创建客户端调试日志与配置参数
        logger.debug(
            "langsmith_client_creating",
            extra={
                "event_fields": {
                    "auto_batch_tracing": auto_batch_tracing,
                    "has_endpoint": bool(self.endpoint),
                    "project": self.project,
                }
            },
        )
        # 第二步：实例化 LangSmith 客户端并注入密钥与端点
        client = Client(
            api_key=self.api_key.get_secret_value() if self.api_key else None,
            api_url=self.endpoint,
            auto_batch_tracing=auto_batch_tracing,
        )
        # 第三步：记录客户端创建完成调试日志并返回
        logger.debug("langsmith_client_created", extra={"event_fields": {"project": self.project}})
        return client


@contextmanager
def tracing_session(settings: LangSmithSettings | None = None) -> Iterator[None]:
    """管理链路追踪（Tracing）会话生命周期；尊重外层 SDK/evaluation 上下文；退出时发送剩余 trace 并释放连接。

    :param settings: 可选的 LangSmith 配置实例，未提供时从环境变量读取。
    :type settings: vertical_harness.observability.LangSmithSettings | None
    :return: 链路追踪上下文管理器迭代器。
    :rtype: collections.abc.Iterator[None]
    :author: tongliga
    :date: 2026-10-19
    """
    # 第一步：检测是否存在外层正在运行的追踪上下文，若存在则直接复用
    if get_tracing_context().get("enabled") is not None:
        logger.debug("tracing_session_reusing_outer_context")
        yield
        return

    # 第二步：解析配置；若配置显式关闭 tracing，则设置禁用上下文短路
    settings = settings or LangSmithSettings()
    if not settings.tracing:
        logger.debug("tracing_session_disabled")
        with tracing_context(enabled=False):
            yield
        return

    # 第三步：启动活跃链路追踪会话，退出时确保清空刷新网络缓冲队列
    logger.debug("tracing_session_starting", extra={"event_fields": {"project": settings.project}})
    with closing(settings.create_client()) as client:
        try:
            with tracing_context(enabled=True, client=client, project_name=settings.project):
                logger.info(
                    "tracing_session_active", extra={"event_fields": {"project": settings.project}}
                )
                yield
        finally:
            logger.debug("tracing_session_flushing")
            client.flush()
            logger.debug("tracing_session_closed")


__all__ = ["LangSmithSettings", "tracing_session"]
