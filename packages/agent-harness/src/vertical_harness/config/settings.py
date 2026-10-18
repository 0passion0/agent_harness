"""经 Pydantic v2 校验的应用程序宿主配置管理模块。"""

from __future__ import annotations

import logging
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from vertical_harness.config.mcp import MCPServerConfig, MCPServerName

logger = logging.getLogger(__name__)


class StoreBackendType(StrEnum):
    """长期记忆与状态存储后端类型枚举。"""

    SQLITE = "sqlite"
    MEMORY = "memory"


class TaskTier(StrEnum):
    """任务质量与计算预算路由层级枚举。"""

    ECONOMY = "economy"
    QUALITY = "quality"


NonEmptyStr = Annotated[str, Field(min_length=1)]
MemoryRouteStr = Annotated[str, Field(pattern=r"^/[a-zA-Z0-9_\-]+/$")]
ModelCallLimit = Annotated[int, Field(ge=1, le=500)]


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
    # 长期记忆与状态存储后端类型：sqlite（默认持久化）或 memory（纯内存）
    store_backend: Literal["sqlite", "memory"] = "sqlite"
    # 自定义 SQLite 数据库文件路径或 ':memory:'
    store_db_path: Path | Literal[":memory:"] | None = None
    # 自定义 LangGraph Checkpointer SQLite 数据库文件路径或 ':memory:'
    checkpoint_db_path: Path | Literal[":memory:"] | None = None
    # LangGraph 检查点持久化同步级别：默认强制开启 'sync' 以保证每步检查点同步落盘（支持 Time Travel / Fork）
    durability: Literal["sync", "async", "exit"] = "sync"
    # LangGraph 工作流插件默认接入模式：'compiled'（本地预编译图）或 'async'（Agent Protocol graph_id）
    workflow_mode: Literal["compiled", "async"] = "compiled"
    # 是否启用纯监督者模式（Supervisor 仅委派已注册工作流，禁用默认 general-purpose 子智能体与直接业务工具）
    supervisor_only: bool = False
    # 长期记忆在虚拟文件系统中的路由路径前缀，默认 '/memories/'
    memory_route: MemoryRouteStr = "/memories/"
    # 关闭后不挂载/召回长期记忆，也不采集偏好或后台整合输入；不删除历史数据。
    enable_memory: bool = True
    # Large knowledge profiles become an index with a full read tool; 0 keeps inline recall.
    memory_inline_max_chars: Annotated[int, Field(ge=0)] = 4_000
    enable_user_preferences: bool = False
    enable_memory_consolidation: bool = False
    # 独立 worker 按每个用户的持久化到期时间运行，默认每 7 天一次。
    memory_consolidation_interval_days: Annotated[int, Field(ge=1, le=365)] = 7
    memory_event_retention_days: Annotated[int, Field(ge=1, le=3650)] = 90
    # 可使用较小的工具调用模型；默认复用主模型及其连接配置。
    memory_model: NonEmptyStr | None = None
    # 宿主显式启用的插件 ID 白名单列表
    enabled_plugins: list[str] = Field(default_factory=list)
    plugin_config: dict[str, dict[str, Any]] = Field(default_factory=dict, repr=False)
    # MCP 服务名到连接配置的映射，VA_MCP_SERVERS 使用 JSON 对象。
    mcp_servers: dict[MCPServerName, MCPServerConfig] = Field(default_factory=dict)
    # 是否启用规划中间件（TodoListMiddleware）
    enable_planning: bool = True
    # 是否启用技能门控（未激活技能时隐藏对应工具）
    enable_skill_gating: bool = True
    # 单次运行允许的最大模型调用次数，防止死循环失控
    max_model_calls: ModelCallLimit = 30
    workflow_timeout_seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)] = 300
    run_timeout_seconds: Annotated[float, Field(gt=0, allow_inf_nan=False)] = 900
    # 应用日志等级、输出格式与可选滚动文件；stdout 专用于业务结果
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_format: Literal["text", "json"] = "text"
    log_file: Path | None = None
    log_max_bytes: Annotated[int, Field(ge=1)] = 10_485_760
    log_backup_count: Annotated[int, Field(ge=1)] = 5

    @property
    def resolved_workspace_root(self) -> Path:
        """解析并返回展开用户目录后的工作区绝对物理路径。

        :return: 工作区根目录的绝对 Path 对象。
        """
        return self.workspace_root.expanduser().resolve()

    @property
    def resolved_store_target(self) -> Path | Literal[":memory:"]:
        """解析 SQLite 数据库在磁盘上的目标路径或内存数据库标识符。

        :return: 解析后的 SQLite 数据库绝对 Path 对象，或内存数据库字面量 ':memory:'。
        """
        if self.store_db_path is None:
            self._check_legacy_database("store.sqlite", "VA_STORE_DB_PATH")
            return self.resolved_runtime_data_dir / "store.sqlite"
        if str(self.store_db_path) == ":memory:":
            return ":memory:"
        return Path(self.store_db_path).expanduser().resolve()

    @property
    def resolved_checkpoint_target(self) -> Path | Literal[":memory:"]:
        """解析 LangGraph Checkpointer SQLite 数据库在磁盘上的目标路径或内存数据库标识符。

        :return: 解析后的检查点 SQLite 数据库绝对 Path 对象，或内存数据库字面量 ':memory:'。
        """
        if self.checkpoint_db_path is None:
            if self.store_backend == StoreBackendType.MEMORY:
                return ":memory:"
            self._check_legacy_database("checkpoints.sqlite", "VA_CHECKPOINT_DB_PATH")
            return self.resolved_runtime_data_dir / "checkpoints.sqlite"
        if str(self.checkpoint_db_path) == ":memory:":
            return ":memory:"
        return Path(self.checkpoint_db_path).expanduser().resolve()

    @property
    def normalized_memory_route(self) -> str:
        """返回规范化为 POSIX 虚拟目录格式的长期记忆路由前缀（如 '/memories/'）。

        :return: 前后均带单斜杠的 POSIX 路由前缀字符串。
        """
        posix_route = PurePosixPath("/", self.memory_route.strip("/"))
        return f"{posix_route}/"

    @property
    def resolved_runtime_data_dir(self) -> Path:
        """Keep host databases outside the model-editable workspace by default."""
        if self.runtime_data_dir is not None:
            return self.runtime_data_dir.expanduser().resolve()
        root = self.resolved_workspace_root
        return root.with_name(f"{root.name}.runtime")

    def _check_legacy_database(self, filename: str, env_name: str) -> None:
        """检查工作区是否存在旧版未隔离的 SQLite 数据库文件，提示显式迁移。

        :param filename: 旧版数据库文件名（如 'store.sqlite'）。
        :param env_name: 对应的显式环境变量名（如 'VA_STORE_DB_PATH'）。
        :raises ValueError: 当工作区根目录下直接存在遗留数据库且未指定运行时数据目录时抛出。
        """
        logger.debug(
            "checking_legacy_database",
            extra={"event_fields": {"filename": filename, "env_name": env_name}},
        )
        if self.runtime_data_dir is None and (self.resolved_workspace_root / filename).exists():
            raise ValueError(
                f"Legacy {filename} exists in workspace; back it up and migrate, or explicitly set {env_name}. No data was moved."
            )

    @model_validator(mode="after")
    def _validate_configuration(self) -> HarnessSettings:
        """配置后置整体校验：确保动态模型成对配置、已配置插件均在启用白名单中。

        :return: 校验通过的当前配置实例。
        :raises ValueError: 当动态模型未成对配置或存在未启用插件的孤立配置时抛出。
        """
        logger.debug("validating_harness_configuration")
        if bool(self.economy_model) != bool(self.quality_model):
            raise ValueError("Both VA_ECONOMY_MODEL and VA_QUALITY_MODEL must be set")
        if unknown := self.plugin_config.keys() - set(self.enabled_plugins):
            raise ValueError(f"Configuration for disabled plugins: {sorted(unknown)}")
        logger.debug("harness_configuration_validated")
        return self

    @property
    def model_init_kwargs(self) -> dict[str, Any]:
        """构造传递给 LangChain `init_chat_model` 的连接凭证与端点参数字典。

        :return: 过滤掉 None 值后的模型初始化关键字参数字典。
        """
        raw_options: dict[str, Any] = {
            "api_key": self.api_key.get_secret_value() if self.api_key is not None else None,
            "base_url": self.base_url,
        }
        return {key: val for key, val in raw_options.items() if val is not None}

    @property
    def has_dynamic_routing(self) -> bool:
        """校验并判断当前配置是否启用了双模型动态路由。

        :return: 若同时配置了经济模型与品质模型则返回 True，均未配置则返回 False。
        :raises ValueError: 当仅配置了经济模型或品质模型其中之一时抛出。
        """
        has_economy = bool(self.economy_model)
        has_quality = bool(self.quality_model)
        if has_economy != has_quality:
            raise ValueError("Both VA_ECONOMY_MODEL and VA_QUALITY_MODEL must be set")
        return has_economy and has_quality


__all__ = [
    "HarnessSettings",
    "MemoryRouteStr",
    "ModelCallLimit",
    "NonEmptyStr",
    "StoreBackendType",
    "TaskTier",
]
