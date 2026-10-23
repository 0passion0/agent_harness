"""LangSmith 追踪连接配置与客户端生成模块。"""

import logging

from langsmith import Client
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class LangSmithSettings(BaseSettings):
    """LangSmith 专用设置项，显式从 .env 文件加载 LANGSMITH_* 配置。"""

    model_config = SettingsConfigDict(env_prefix="LANGSMITH_", env_file=".env", extra="ignore")

    tracing: bool = False
    api_key: SecretStr | None = None
    endpoint: str | None = None
    project: str = "vertical-research-agent"

    def create_client(self, *, auto_batch_tracing: bool = True) -> Client:
        """根据配置的凭据和服务端点构建 LangSmith 客户端。"""
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
