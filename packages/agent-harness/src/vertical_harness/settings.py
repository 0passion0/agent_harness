"""经 Pydantic 校验的应用程序配置管理模块。"""

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    model: str = Field(min_length=1)
    # 模型服务商 API 密钥，使用 SecretStr 保护避免日志意外泄露
    api_key: SecretStr | None = None
    # 自定义 API 服务端点 Base URL
    base_url: str | None = None
    # 动态路由中的经济型模型标识符（如 'deepseek:deepseek-chat'）
    economy_model: str | None = Field(default=None, min_length=1)
    # 动态路由中的高品质型模型标识符（如 'deepseek:deepseek-reasoner'）
    quality_model: str | None = Field(default=None, min_length=1)
    # 智能体运行的本地工作区物理根路径，默认 './.agent-workspace'
    workspace_root: Path = Path("./.agent-workspace")
    # 长期记忆与状态存储后端类型：sqlite（默认持久化）或 memory（纯内存）
    store_backend: Literal["sqlite", "memory"] = "sqlite"
    # 自定义 SQLite 数据库文件路径或 ':memory:'
    store_db_path: Path | Literal[":memory:"] | None = None
    # 长期记忆在虚拟文件系统中的路由路径前缀，默认 '/memories/'
    memory_route: str = Field(default="/memories/", pattern=r"^/[a-zA-Z0-9_\-]+/$")
    # 宿主显式启用的插件 ID 白名单列表
    enabled_plugins: list[str] = Field(default_factory=lambda: ["research.citations"])
    # 是否启用规划中间件（TodoListMiddleware）
    enable_planning: bool = True
    # 是否启用技能门控（未激活技能时隐藏对应工具）
    enable_skill_gating: bool = True
    # 单次运行允许的最大模型调用次数，防止死循环失控
    max_model_calls: int = Field(default=30, ge=1, le=500)

    @property
    def resolved_store_target(self) -> Path | Literal[":memory:"]:
        """解析 SQLite 数据库在磁盘上的目标路径或内存数据库标识符。

        :return: 解析后的 SQLite 数据库绝对 Path 对象，或内存数据库字面量 ':memory:'。
        """
        # 若显式配置了 store_db_path，则优先按配置解析
        if self.store_db_path is not None:
            # 如果配置为 ':memory:'，直接返回内存数据库标记
            if str(self.store_db_path) == ":memory:":
                return ":memory:"
            # 否则将其展开并解析为磁盘绝对路径
            return Path(self.store_db_path).expanduser().resolve()
        # 默认在 workspace_root 目录下创建 store.sqlite 文件
        return self.workspace_root.expanduser().resolve() / "store.sqlite"


