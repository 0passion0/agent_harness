"""已安装领域插件的强类型贡献契约与 LangChain / Deep Agents 组件集成定义模块。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal, Protocol, TypeAlias, runtime_checkable

from deepagents import CompiledSubAgent
from langchain.agents.middleware import AgentMiddleware, InterruptOnConfig
from langchain_core.tools import BaseTool, BaseToolkit
from langgraph.store.base import BaseStore
from pydantic import BaseModel, ConfigDict, Field, field_validator

# 技能路径各分段合法字符正则：必须包含至少一个字母或数字，且仅允许字母、数字、点、下划线、破折号
_SAFE_SKILL_SEGMENT = re.compile(r"^(?=.*[A-Za-z0-9])[A-Za-z0-9._-]+$")

# 插件全局唯一标识符类型约束
PluginIdStr = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.-]+$")]
# 语义化版本号（SemVer）类型约束
SemVerStr = Annotated[str, Field(pattern=r"^\d+\.\d+\.\d+$")]
# 子智能体规范联合类型（兼容 Deep Agents SubAgent/AsyncSubAgent 字典规范及预编译 CompiledSubAgent）
SubAgentSpec: TypeAlias = dict[str, Any] | CompiledSubAgent


class StrictModel(BaseModel):
    """禁止未知字段的严格基础模型。"""

    # extra='forbid' 禁止任何未声明的多余字段，提升契约严格度；允许任意类型兼容 LangChain 原生对象
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)


class SideEffect(StrEnum):
    """工具操作副作用分类，用于权限与人工审批（HITL）判定。"""

    # 纯只读操作，无需审批
    READ = "read"
    # 本地或工作区写操作，需要审批
    WRITE = "write"
    # 外部网络或第三方系统交互，需要审批
    EXTERNAL = "external"

    @property
    def requires_approval(self) -> bool:
        """判定当前副作用级别是否必须触发 LangChain 人工审批中断。

        :return: 若为 WRITE 或 EXTERNAL 则返回 True，否则返回 False。
        """
        return self in {SideEffect.WRITE, SideEffect.EXTERNAL}


class Exposure(StrEnum):
    """控制工具对模型的可见时机。"""

    # 智能体启动后直接对大模型可见
    EAGER = "eager"
    # 仅当对应技能（Skill）激活时才对大模型可见
    SKILL = "skill"


class PluginManifest(StrictModel):
    """独立安装插件的稳定元数据描述清单。"""

    # 插件全局唯一标识符，小写字母开头，仅允许小写字母、数字、点、减号、下划线
    plugin_id: PluginIdStr
    # 插件契约 API 版本号，当前固定为 1
    api_version: Annotated[int, Field(ge=1, le=1)] = 1
    # 插件语义化版本号（SemVer）
    version: SemVerStr
    # 插件功能描述文本，最小长度 8 字符
    description: Annotated[str, Field(min_length=8)]

    @property
    def normalized_prefix(self) -> str:
        """将插件 ID 规范化为虚拟文件系统安全的命名前缀（将 '.' 替换为 '-'）。

        :return: 规范化后的插件前缀字符串。
        """
        return self.plugin_id.replace(".", "-")


class SkillAsset(StrictModel):
    """随插件打包并在启动时挂载到虚拟 /skills 目录下的技能资产文件。"""

    # 技能相对路径，必须位于技能子目录下，如 'citation-audit/SKILL.md'
    relative_path: str
    # 技能文件的完整文本内容
    content: str

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        """验证技能资产的相对路径，防范目录遍历与绝对路径注入。

        :param value: 待校验的技能文件相对路径字符串。
        :return: 校验合法的相对路径字符串。
        :raises ValueError: 当路径为绝对路径、包含 '.' 或 '..' 遍历路径、层级少于两级或包含非法特殊字符时抛出。
        """
        posix_path = PurePosixPath(value)
        parts = posix_path.parts
        # 严禁绝对路径、反斜杠路径、包含 '.' 或 '..'，且路径至少包含两级（如 skill_name/file）
        if (
            "\\" in value
            or posix_path.is_absolute()
            or any(part in {".", ".."} for part in parts)
            or len(parts) < 2
        ):
            raise ValueError("Skill path must be a relative path inside a skill directory")
        # 路径各分段仅允许字母数字及破折号、下划线、点，且至少包含一个字母或数字
        if any(_SAFE_SKILL_SEGMENT.fullmatch(part) is None for part in parts):
            raise ValueError("Invalid skill path component")
        return str(posix_path)

    def to_virtual_path(self, plugin_id: str, skills_root: str = "/skills") -> str:
        """结合插件 ID 生成命名空间隔离后的虚拟文件系统路径。

        :param plugin_id: 所属插件的唯一标识符。
        :param skills_root: 虚拟技能根目录路径，默认为 '/skills'。
        :return: 隔离后的虚拟文件完整路径，如 '/skills/research-citations-numeric-citation-audit/SKILL.md'。
        """
        posix_path = PurePosixPath(self.relative_path)
        namespaced_dir = f"{plugin_id.replace('.', '-')}-{posix_path.parts[0]}"
        return str(PurePosixPath(skills_root, namespaced_dir, *posix_path.parts[1:]))

    def to_upload_payload(self, plugin_id: str, skills_root: str = "/skills") -> tuple[str, bytes]:
        """生成可直接传递给 Deep Agents Backend `upload_files` 的 (虚拟路径, UTF-8 字节流) 二元组。

        :param plugin_id: 所属插件的唯一标识符。
        :param skills_root: 虚拟技能根目录路径，默认为 '/skills'。
        :return: (虚拟路径字符串, UTF-8 编码字节内容) 元组。
        """
        return (self.to_virtual_path(plugin_id, skills_root=skills_root), self.content.encode("utf-8"))


class ToolContribution(StrictModel):
    """已注册的 LangChain 工具及其暴露级别和审批元数据。"""

    # LangChain 原生工具实例
    tool: BaseTool
    # 工具副作用分类，默认为只读 READ
    effect: SideEffect = SideEffect.READ
    # 工具暴露时机，默认为直接暴露 EAGER
    exposure: Exposure = Exposure.EAGER

    @property
    def name(self) -> str:
        """获取底层 LangChain BaseTool 的注册名称。

        :return: 工具名称字符串。
        """
        return self.tool.name

    @property
    def requires_approval(self) -> bool:
        """判断该工具是否需要启用人工审批中断（Human-in-the-Loop）。

        :return: 若工具具有写或外部副作用则返回 True。
        """
        return self.effect.requires_approval

    def to_interrupt_config(self) -> InterruptOnConfig:
        """构造 LangChain `HumanInTheLoopMiddleware` 原生兼容的审批中断配置字典。

        :return: 允许 approve 与 reject 决策的 InterruptOnConfig 配置。
        """
        return InterruptOnConfig(allowed_decisions=["approve", "reject"])

    @classmethod
    def from_toolkit(
        cls,
        toolkit: BaseToolkit,
        *,
        effect: SideEffect = SideEffect.READ,
        exposure: Exposure = Exposure.EAGER,
    ) -> list[ToolContribution]:
        """从 LangChain 标准 `BaseToolkit` 批量导出并包装工具贡献列表。

        :param toolkit: 遵循 LangChain BaseToolkit 接口的工具包实例。
        :param effect: 批量应用的工具副作用分类，默认只读 READ。
        :param exposure: 批量应用的工具暴露模式，默认直接暴露 EAGER。
        :return: 包装后的 ToolContribution 实例列表。
        """
        return [cls(tool=t, effect=effect, exposure=exposure) for t in toolkit.get_tools()]


class PluginContribution(StrictModel):
    """插件向宿主框架贡献的所有原生组件集合。"""

    # 插件贡献的原生工具列表
    tools: list[ToolContribution] = Field(default_factory=list)
    # 插件贡献的技能资产文件列表
    skills: list[SkillAsset] = Field(default_factory=list)
    # 插件贡献的 LangChain / Deep Agents 中间件列表
    middleware: list[AgentMiddleware[Any, Any, Any]] = Field(default_factory=list)
    # 插件贡献的子智能体定义或预编译子智能体列表
    subagents: list[SubAgentSpec] = Field(default_factory=list)
    # 插件追加至主智能体的专用系统指令说明
    instructions: str = ""

    def format_system_instruction(self, plugin_id: str) -> str | None:
        """生成带有插件命名空间标识的系统提示词段落。

        :param plugin_id: 插件唯一标识符。
        :return: 若配置了 instructions 则返回带前缀的指令字符串，否则返回 None。
        """
        cleaned = self.instructions.strip()
        return f"[{plugin_id}] {cleaned}" if cleaned else None


@dataclass(frozen=True, slots=True)
class BuildServices:
    """宿主在构建期提供的无单例依赖服务集合。"""

    # 智能体运行的工作区绝对物理根目录
    workspace_root: Path
    # 可选的长期记忆及键值持久化存储 BaseStore 实例
    store: BaseStore | None = None


@dataclass(frozen=True, slots=True)
class RunContext:
    """每次智能体运行由 LangGraph 注入的可信运行时上下文（非模型生成参数）。"""

    # 隔离租户标识符
    tenant_id: str
    # 隔离用户标识符
    user_id: str
    # 任务质量/预算层级：economy（经济型）或 quality（高品质型）
    task_tier: Literal["economy", "quality"] = "quality"


@runtime_checkable
class DomainPlugin(Protocol):
    """Python 打包入口点遵循的领域插件协议规范。"""

    # 插件元数据描述清单
    manifest: PluginManifest

    def build(self, services: BuildServices) -> PluginContribution:
        """创建该插件向 Deep Agents / LangChain 提供的原生贡献。

        :param services: 宿主在构建期注入的服务实例集合。
        :return: 包含工具、技能、中间件、子智能体与系统指令的插件贡献声明对象。
        """
        ...


__all__ = [
    "BuildServices",
    "DomainPlugin",
    "Exposure",
    "PluginContribution",
    "PluginIdStr",
    "PluginManifest",
    "RunContext",
    "SemVerStr",
    "SideEffect",
    "SkillAsset",
    "StrictModel",
    "SubAgentSpec",
    "ToolContribution",
]
