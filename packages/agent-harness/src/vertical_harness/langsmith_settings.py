"""Configuration for LangSmith tracing connections."""

import logging

from langsmith import Client
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class LangSmithSettings(BaseSettings):
    """Explicitly load .env so tracing configuration is not limited to process env."""

    model_config = SettingsConfigDict(env_prefix="LANGSMITH_", env_file=".env", extra="ignore")

    tracing: bool = False
    api_key: SecretStr | None = None
    endpoint: str | None = None
    project: str = "vertical-research-agent"

    def create_client(self, *, auto_batch_tracing: bool = True) -> Client:
        """Build a LangSmith client from the loaded credentials and endpoint."""
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
