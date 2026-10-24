"""经 Pydantic v2 校验的应用程序宿主配置管理模块。

本模块定义了整个 Agent Harness 系统的核心配置模型 `HarnessSettings`。
配置从以 `VA_` 为前缀的环境变量或 `.env` 配置文件中读取，并执行强类型校验与逻辑约束检查。
支持动态模型路由配置验证、工作区与运行时数据目录路径规范化、持久化存储路径计算等能力。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from vertical_harness.config.store_backend import StoreBackendType
from vertical_harness.models.formatter_entry import FormatterEntry

# 模块级日志记录器，记录配置校验与解析事件
logger = logging.getLogger(__name__)

# 约束最小长度为 1 的非空字符串类型注解，防止环境变量配置为空字符串
NonEmptyStr = Annotated[str, Field(min_length=1)]


class HarnessSettings(BaseSettings):
    """严格从 VA_* 环境变量以及 .env 文件加载的应用宿主全局配置类。

    继承自 Pydantic 的 `BaseSettings`，管理模型标识、API 凭证、执行工作区路径、
    持久化后端、权限模式、上下文 Token 预算上限以及日志策略等配置项。

    Attributes:
        model (NonEmptyStr): 默认主大语言模型标识符，格式如 `'deepseek:deepseek-chat'`，必填项。
        api_key (SecretStr | None): 模型服务商鉴权密钥，使用 `SecretStr` 封装以防日志意外泄漏。
        base_url (str | None): 自定义模型推理 API 服务端点根 URL，若使用官方默认端点则为 None。
        economy_model (NonEmptyStr | None): 动态路由中用于低计算复杂度任务的经济型模型标识。
        quality_model (NonEmptyStr | None): 动态路由中用于复杂任务或反思推理的高质量模型标识。
        workspace_root (Path): 智能体文件系统操作沙箱的工作区绝对或相对物理根路径。
        runtime_data_dir (Path | None): 智能体运行时内部数据目录（如数据库与锁），隔离在工作区之外。
        python_executable (NonEmptyStr): 受控 Python 解释器执行路径，默认为当前 Python 进程路径。
        store_backend (Literal["sqlite", "memory"]): 状态键值存储后端类型：`sqlite` 或 `memory`。
        store_db_path (Path | Literal[":memory:"] | None): SQLite 存储数据库文件路径或 `":memory:"`。
        checkpoint_db_path (Path | Literal[":memory:"] | None): LangGraph 检查点 SQLite 文件路径。
        durability (Literal["sync", "async", "exit"]): 检查点同步落盘策略，默认 `sync` 确保安全。
        permission_mode (Literal["read_only", "ask", "full"]): 文件与命令执行权限拦截模式。
        max_context_tokens (int): 单次模型请求的上下文 Token 预算硬上限，默认 475,000。
        agent_content (str): 追加到系统提示词尾部的自定义业务或角色指令文本。
        node_executable (str): Node.js 解释器路径，用于驱动 Prettier 等外部代码格式化器。
        enable_planning (bool): 是否激活 TodoListMiddleware 任务拆解与规划中间件。
        max_steps (int | None): 智能体单次交互的最大执行步数配额，达到上限后强制截断。
        formatter (bool | dict[str, FormatterEntry] | None): 外部代码格式化器配置或总开关。
        run_timeout_seconds (float): 单次任务执行总超时时间（秒），默认 900 秒。
        log_level (Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]): 宿主日志输出级别。
        log_format (Literal["text", "json"]): 日志格式：可读文本 `text` 或结构化 `json`。
        log_file (Path | None): 滚动日志文件输出路径；若为 None 则仅输出至控制台 stderr。
        log_max_bytes (int): 单个滚动日志文件的最大字节数，默认 10MB。
        log_backup_count (int): 轮转日志保留的最大历史备份文件数量。
    """

    # 配置环境变量加载规则：以 'VA_' 为前缀，编码 utf-8，忽略未定义的额外环境变量
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
    # 运行时数据隔离目录，用于持久化数据库与状态，隔离在工作区之外
    runtime_data_dir: Path | None = None
    # 受控 Python 脚本执行器解释器路径
    python_executable: NonEmptyStr = sys.executable
    # 状态存储后端类型：sqlite（默认持久化）或 memory（纯内存）
    store_backend: Literal["sqlite", "memory"] = "sqlite"
    # 自定义 SQLite 数据库文件路径或 ':memory:'
    store_db_path: Path | Literal[":memory:"] | None = None
    # 自定义 LangGraph Checkpointer SQLite 数据库文件路径或 ':memory:'
    checkpoint_db_path: Path | Literal[":memory:"] | None = None
    # LangGraph 检查点持久化同步级别：默认强制开启 'sync' 以保证每步检查点同步落盘
    durability: Literal["sync", "async", "exit"] = "sync"
    # 权限拦截模式（read_only 只读、ask 询问审批、full 完全授权）
    permission_mode: Literal["read_only", "ask", "full"] = "full"
    # 单次请求上下文 Token 估算上限，超限触发硬限制拦截
    max_context_tokens: Annotated[int, Field(strict=True, ge=1, le=475_000)] = 475_000
    # 追加到系统提示词尾部的自定义业务或角色指令
    agent_content: str = ""
    # Node 运行时解释器路径（用于调用基于 Node 的格式化工具）
    node_executable: str = ""
    # 是否启用 TodoListMiddleware 任务规划能力
    enable_planning: bool = True
    # 任务最大执行步数限制，超出后强制收尾并退出
    max_steps: Annotated[int, Field(ge=1)] | None = None
    # 外部代码格式化器配置或启用开关
    formatter: bool | dict[str, FormatterEntry] | None = None
    # 单次运行最大执行超时时间（秒）
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
        """预处理环境变量字符串格式的上下文 Token 上限数值。

        环境变量在读取阶段默认为原始字符串格式，此校验器将其转换为整型后再由 Pydantic 执行范围约束校验。

        Args:
            value (object): 待解析的上下文预算值，可能是来自环境变量的 `str` 或代码传入的 `int`。

        Returns:
            object: 转换为整数的数值或原始对象（若非字符串格式）。
        """
        # 环境变量始终是字符串；其余输入仍由严格整数约束校验。
        return int(value) if isinstance(value, str) else value

    @property
    def resolved_workspace_root(self) -> Path:
        """解析并返回展开用户目录后的工作区绝对物理路径。

        Returns:
            Path: 解析后的绝对物理路径，消除了 `~` 缩写与相对路径符号 `.` / `..`。
        """
        return self.workspace_root.expanduser().resolve()

    @property
    def resolved_store_target(self) -> Path | Literal[":memory:"]:
        """解析 SQLite 键值数据库在磁盘上的目标绝对路径或内存标识符。

        如果未显式配置 `store_db_path`，默认将数据库存储在 `resolved_runtime_data_dir / "store.sqlite"`。

        Returns:
            Path | Literal[":memory:"]: 磁盘上的绝对文件路径，或者 `":memory:"` 内存标识符。
        """
        if self.store_db_path is None:
            return self.resolved_runtime_data_dir / "store.sqlite"
        if str(self.store_db_path) == ":memory:":
            return ":memory:"
        return Path(self.store_db_path).expanduser().resolve()

    @property
    def resolved_checkpoint_target(self) -> Path | Literal[":memory:"]:
        """解析 LangGraph Checkpointer SQLite 数据库在磁盘上的目标路径或内存数据库标识符。

        如果未显式配置 `checkpoint_db_path`：
        - 若 `store_backend` 为内存模式，则返回 `":memory:"`；
        - 否则默认落盘至 `resolved_runtime_data_dir / "checkpoints.sqlite"`。

        Returns:
            Path | Literal[":memory:"]: 检查点数据库在磁盘上的绝对路径或内存标识符。
        """
        if self.checkpoint_db_path is None:
            if self.store_backend == StoreBackendType.MEMORY:
                return ":memory:"
            return self.resolved_runtime_data_dir / "checkpoints.sqlite"
        if str(self.checkpoint_db_path) == ":memory:":
            return ":memory:"
        return Path(self.checkpoint_db_path).expanduser().resolve()

    @property
    def resolved_runtime_data_dir(self) -> Path:
        """解析并返回运行时数据目录绝对路径。

        若未显式指定 `runtime_data_dir`，则根据工作区同级目录创建 `<workspace_name>.runtime`，
        将状态数据库与临时文件隔离在工作区之外，防止污染用户项目代码仓库。

        Returns:
            Path: 运行时数据目录绝对路径。
        """
        if self.runtime_data_dir is not None:
            return self.runtime_data_dir.expanduser().resolve()
        root = self.resolved_workspace_root
        return root.with_name(f"{root.name}.runtime")

    @model_validator(mode="after")
    def _validate_configuration(self) -> HarnessSettings:
        """配置后置整体约束校验：确保动态模型成对配置。

        动态路由依赖经济型和质量型两个模型；必须同时配置或同时留空。

        Returns:
            HarnessSettings: 校验通过后的自身配置实例。

        Raises:
            ValueError: 当仅配置了 `economy_model` 或 `quality_model` 其中之一时抛出。
        """
        logger.debug("validating_harness_configuration")
        if bool(self.economy_model) != bool(self.quality_model):
            raise ValueError("Both VA_ECONOMY_MODEL and VA_QUALITY_MODEL must be set")
        logger.debug("harness_configuration_validated")
        return self

    @property
    def model_init_kwargs(self) -> dict[str, Any]:
        """构造传递给 LangChain `init_chat_model` 的连接凭证与端点参数字典。

        提取 `api_key`（明文字符串）和 `base_url`（若存在），自动过滤掉值为 None 的项。

        Returns:
            dict[str, Any]: 仅包含已配置项的字典，例如 `{"api_key": "...", "base_url": "..."}`。
        """
        raw_options: dict[str, Any] = {
            "api_key": self.api_key.get_secret_value() if self.api_key is not None else None,
            "base_url": self.base_url,
        }
        return {key: val for key, val in raw_options.items() if val is not None}

    @property
    def has_dynamic_routing(self) -> bool:
        """校验并判断当前配置是否启用了双模型动态路由。

        Returns:
            bool: 若已成对配置经济型与质量型模型则返回 True，否则返回 False。
        """
        return bool(self.economy_model)
