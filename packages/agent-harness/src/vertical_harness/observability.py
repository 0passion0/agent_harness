"""LangSmith 连接和 tracing 生命周期；模型连接始终由 VA_* 配置管理。"""

from collections.abc import Iterator
from contextlib import closing, contextmanager

from langsmith import Client, tracing_context
from langsmith.run_helpers import get_tracing_context
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class LangSmithSettings(BaseSettings):
    """显式读取 .env，避免仅由 SDK 读取进程环境而漏掉 tracing 配置。"""

    model_config = SettingsConfigDict(env_prefix="LANGSMITH_", env_file=".env", extra="ignore")

    tracing: bool = False
    api_key: SecretStr | None = None
    endpoint: str | None = None
    project: str = "vertical-research-agent"

    def create_client(self, *, auto_batch_tracing: bool = True) -> Client:
        return Client(
            api_key=self.api_key.get_secret_value() if self.api_key else None,
            api_url=self.endpoint,
            auto_batch_tracing=auto_batch_tracing,
        )


@contextmanager
def tracing_session(settings: LangSmithSettings | None = None) -> Iterator[None]:
    """尊重外层 SDK/evaluation 上下文；退出时发送剩余 trace 并释放连接。"""
    if get_tracing_context().get("enabled") is not None:
        yield
        return
    settings = settings or LangSmithSettings()
    if not settings.tracing:
        with tracing_context(enabled=False):
            yield
        return
    with closing(settings.create_client()) as client:
        try:
            with tracing_context(enabled=True, client=client, project_name=settings.project):
                yield
        finally:
            client.flush()


__all__ = ["LangSmithSettings", "tracing_session"]
