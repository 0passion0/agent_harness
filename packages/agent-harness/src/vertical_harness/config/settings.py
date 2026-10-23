"""经 Pydantic v2 校验的应用程序宿主配置管理模块。"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from vertical_harness.config.formatter_entry import FormatterEntry
from vertical_harness.config.store_backend import StoreBackendType

logger = logging.getLogger(__name__)

NonEmptyStr = Annotated[str, Field(min_length=1)]


class HarnessSettings(BaseSettings):
    """严格从 VA_* 环境变量以及 .env 文件加载的应用宿主配置类。"""

    # 配置环境变量加载规则：以 'VA_' 为前缀，编码 utf-8，忽略无关的环境变量
    model_config = SettingsConfigDict(
        env_prefix="VA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 默认主大模型标识符，如 'deepseek:deepseek-chat'，必填
    model: NonEmptyStr
    # 模型服务商 API 密钥，使用 SecretStr 保护避免日志意外泄露
    api_key: SecretStr | None = None
    # 自定义 API 服务端点 Base URL
    base_url: str | None = None
    # 动态路由中的经济型模型标识符（如 'deepseek:deepseek-chat'）
    economy_model: NonEmptyStr | None = None
    # 动态路由中的高品质型模型标识符（如 'deepseek:deepseek-reasoner'）
    quality_model: NonEmptyStr | None = None
    # 智能体运行的本地工作区物理根路径，默认 './.agent-workspace'
    workspace_root: Path = Path("./.agent-workspace")
    runtime_data_dir: Path | None = None
    python_executable: NonEmptyStr = sys.executable
    # 状态存储后端类型：sqlite（默认持久化）或 memory（纯内存）
    store_backend: Literal["sqlite", "memory"] = "sqlite"
    # 自定义 SQLite 数据库文件路径或 ':memory:'
    store_db_path: Path | Literal[":memory:"] | None = None
    # 自定义 LangGraph Checkpointer SQLite 数据库文件路径或 ':memory:'
    checkpoint_db_path: Path | Literal[":memory:"] | None = None
    # LangGraph 检查点持久化同步级别：默认强制开启 'sync' 以保证每步检查点同步落盘
    durability: Literal["sync", "async", "exit"] = "sync"
    permission_mode: Literal["read_only", "ask", "full"] = "full"
    max_context_tokens: Annotated[int, Field(strict=True, ge=1, le=475_000)] = 475_000
    agent_content: str = ""
    node_executable: str = ""
    enable_planning: bool = True
    max_steps: Annotated[int, Field(ge=1)] | None = None
    formatter: bool | dict[str, FormatterEntry] | None = None
    run_timeout_seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)] = 900
    # 应用日志等级、输出格式与可选滚动文件；stdout 专用于业务结果
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_format: Literal["text", "json"] = "text"
    log_file: Path | None = None
    log_max_bytes: Annotated[int, Field(ge=1)] = 10_485_760
    log_backup_count: Annotated[int, Field(ge=1)] = 5

    @field_validator("max_context_tokens", mode="before")
    @classmethod
    def _parse_context_budget(cls, value: object) -> object:
        # 环境变量始终是字符串；其余输入仍由严格整数约束校验。
        return int(value) if isinstance(value, str) else value

    @property
    def resolved_workspace_root(self) -> Path:
        """解析并返回展开用户目录后的工作区绝对物理路径。"""
        return self.workspace_root.expanduser().resolve()

    @property
    def resolved_store_target(self) -> Path | Literal[":memory:"]:
        """解析 SQLite 数据库在磁盘上的目标路径或内存数据库标识符。"""
        if self.store_db_path is None:
            return self.resolved_runtime_data_dir / "store.sqlite"
        if str(self.store_db_path) == ":memory:":
            return ":memory:"
        return Path(self.store_db_path).expanduser().resolve()

    @property
    def resolved_checkpoint_target(self) -> Path | Literal[":memory:"]:
        """解析 LangGraph Checkpointer SQLite 数据库在磁盘上的目标路径或内存数据库标识符。"""
        if self.checkpoint_db_path is None:
            if self.store_backend == StoreBackendType.MEMORY:
                return ":memory:"
            return self.resolved_runtime_data_dir / "checkpoints.sqlite"
        if str(self.checkpoint_db_path) == ":memory:":
            return ":memory:"
        return Path(self.checkpoint_db_path).expanduser().resolve()

    @property
    def resolved_runtime_data_dir(self) -> Path:
        """解析并返回运行时数据目录绝对路径（默认将宿主数据库隔离在工作区之外）。"""
        if self.runtime_data_dir is not None:
            return self.runtime_data_dir.expanduser().resolve()
        root = self.resolved_workspace_root
        return root.with_name(f"{root.name}.runtime")

    @model_validator(mode="after")
    def _validate_configuration(self) -> HarnessSettings:
        """配置后置整体校验：确保动态模型成对配置。"""
        logger.debug("validating_harness_configuration")
        if bool(self.economy_model) != bool(self.quality_model):
            raise ValueError("Both VA_ECONOMY_MODEL and VA_QUALITY_MODEL must be set")
        logger.debug("harness_configuration_validated")
        return self

    @property
    def model_init_kwargs(self) -> dict[str, Any]:
        """构造传递给 LangChain `init_chat_model` 的连接凭证与端点参数字典。"""
        raw_options: dict[str, Any] = {
            "api_key": self.api_key.get_secret_value() if self.api_key is not None else None,
            "base_url": self.base_url,
        }
        return {key: val for key, val in raw_options.items() if val is not None}

    @property
    def has_dynamic_routing(self) -> bool:
        """校验并判断当前配置是否启用了双模型动态路由。"""
        return bool(self.economy_model)
