"""LangSmith 追踪连接配置与客户端生成模块。"""

import logging

from langsmith import Client
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class LangSmithSettings(BaseSettings):
    """LangSmith 专用设置项，显式从 .env 文件加载 LANGSMITH_* 配置。

    Attributes:
        tracing: 是否全局开启 LangSmith 链路追踪，默认为 False。
        api_key: LangSmith 访问凭据密钥，由 SecretStr 包装保护。
        endpoint: 可选的私有部署或自定义 LangSmith 服务端点 URL。
        project: 追踪会话归属的项目名称，默认 'vertical-research-agent'。
    """

    model_config = SettingsConfigDict(env_prefix="LANGSMITH_", env_file=".env", extra="ignore")

    tracing: bool = False
    api_key: SecretStr | None = None
    endpoint: str | None = None
    project: str = "vertical-research-agent"

    def create_client(self, *, auto_batch_tracing: bool = True) -> Client:
        """根据配置的凭据和服务端点构建 LangSmith 客户端。

        Args:
            auto_batch_tracing: 是否在后台自动批量发送追踪事件帧，默认为 True。

        Returns:
            Client: 初始化的 LangSmith 官方客户端实例。
        """
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
        client = Client(
            api_key=self.api_key.get_secret_value() if self.api_key else None,
            api_url=self.endpoint,
            auto_batch_tracing=auto_batch_tracing,
        )
        logger.debug("langsmith_client_created", extra={"event_fields": {"project": self.project}})
        return client
