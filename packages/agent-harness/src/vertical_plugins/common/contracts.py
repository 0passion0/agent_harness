"""已安装领域插件的强类型贡献契约与 LangChain / Deep Agents 组件集成定义模块。"""

from __future__ import annotations

import json
import logging
import re
import sys
from copy import deepcopy
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    Literal,
    NotRequired,
    Self,
    TypeAlias,
)

from deepagents import AsyncSubAgent, CompiledSubAgent
from langchain.agents.middleware import AgentMiddleware, InterruptOnConfig
from langchain_core.messages import AnyMessage
from langchain_core.runnables import Runnable, RunnableBinding
from langchain_core.tools import BaseTool, BaseToolkit
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.base import BaseStore
from langgraph.types import Durability
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from typing_extensions import TypedDict

from vertical_plugins.common.prompts import format_plugin_instruction

logger = logging.getLogger(__name__)

# 技能路径各分段合法字符正则：必须包含至少一个字母或数字，且仅允许字母、数字、点、下划线、破折号
_SAFE_SKILL_SEGMENT = re.compile(r"^(?=.*[A-Za-z0-9])[A-Za-z0-9._-]+$")
# 保留的内置子智能体名称，插件或工作流不得覆盖
_RESERVED_SUBAGENT_NAMES = frozenset({"general-purpose"})

# 插件全局唯一标识符类型约束
PluginIdStr = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.-]+$")]
# 工作流或子智能体标识符类型约束
WorkflowNameStr = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.-]*$")]
# 语义化版本号（SemVer）类型约束
SemVerStr = Annotated[str, Field(pattern=r"^\d+\.\d+\.\d+$")]
# 子智能体上下文传递模式：isolated（仅传递任务描述）或 fork（继承父对话上下文快照）
SubAgentContextMode: TypeAlias = Literal["isolated", "fork"]
# 子智能体规范联合类型（兼容 Deep Agents SubAgent/AsyncSubAgent 字典规范及预编译 CompiledSubAgent）
# 注意：Python < 3.12 下 Pydantic 拒绝为标准库 typing.TypedDict（如 deepagents 中的 CompiledSubAgent/AsyncSubAgent）
# 生成 schema，导致包含 list[SubAgentSpec] 的 Pydantic 模型（PluginContribution）在类构建时抛出 PydanticUserError。
# 为确保静态类型检查与运行时跨 Python 版本完全兼容，静态分析（TYPE_CHECKING）保留完整联合类型，运行时采用 dict[str, Any]。
if TYPE_CHECKING:
    SubAgentSpec: TypeAlias = dict[str, Any] | CompiledSubAgent | AsyncSubAgent
else:
    SubAgentSpec: TypeAlias = dict[str, Any]


class WorkflowInputState(TypedDict):
    """定义工作流调用方需要提供的数据（input_schema 基类，兼容 Deep Agents messages 协议）。"""

    messages: Annotated[list[AnyMessage], add_messages]


class WorkflowOutputState(TypedDict):
    """定义工作流最终返回给调用方/主智能体的数据（output_schema 基类）。"""

    messages: Annotated[list[AnyMessage], add_messages]
    structured_response: NotRequired[Any]


class WorkflowInternalState(WorkflowOutputState):
    """定义工作流内部节点流转使用的数据（InternalState 基类，不直接暴露给外部调用方）。"""

    internal_context: NotRequired[dict[str, Any]]


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
        :rtype: bool
        :author: tongliga
        :date: 2026-10-19
        """
        return self in {SideEffect.WRITE, SideEffect.EXTERNAL}


class Exposure(StrEnum):
    """控制工具对模型的可见时机。"""

    # 智能体启动后直接对大模型可见
    EAGER = "eager"
    # 仅当对应技能（Skill）激活时才对大模型可见
    SKILL = "skill"


class WorkflowMode(StrEnum):
    """LangGraph 工作流插件接入 Deep Agents Supervisor 的运行模式枚举。"""

    # 本地单进程直接挂载预编译 LangGraph（CompiledSubAgent，通过 task 同步/异步等待结果）
    COMPILED = "compiled"
    # 通过 Agent Protocol / graph_id 接入远程或本地 ASGI 异步任务（AsyncSubAgent）
    ASYNC = "async"


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
        :rtype: str
        :author: tongliga
        :date: 2026-10-19
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
        :type value: str
        :return: 校验合法的相对路径字符串。
        :rtype: str
        :raises ValueError: 当路径为绝对路径、包含 '.' 或 '..' 遍历路径、层级少于两级或包含非法特殊字符时抛出。
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：记录路径校验启动调试日志
        logger.debug(
            "validate_relative_path_started",
            extra={"event_fields": {"has_path": bool(value)}},
        )
        posix_path = PurePosixPath(value)
        parts = posix_path.parts

        # 第二步：断言防范绝对路径注入、反斜杠与父级目录遍历
        if (
            "\\" in value
            or posix_path.is_absolute()
            or any(part in {".", ".."} for part in parts)
            or len(parts) < 2
        ):
            raise ValueError("Skill path must be a relative path inside a skill directory")

        # 第三步：逐级校验路径分段字符合法性
        if any(_SAFE_SKILL_SEGMENT.fullmatch(part) is None for part in parts):
            raise ValueError("Invalid skill path component")
        return str(posix_path)

    def to_virtual_path(self, plugin_id: str, skills_root: str = "/skills") -> str:
        """结合插件 ID 生成命名空间隔离后的虚拟文件系统路径。

        :param plugin_id: 所属插件的唯一标识符。
        :type plugin_id: str
        :param skills_root: 虚拟技能根目录路径，默认为 '/skills'。
        :type skills_root: str
        :return: 隔离后的虚拟文件完整路径，如 '/skills/research-citations-numeric-citation-audit/SKILL.md'。
        :rtype: str
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：计算带插件命名前缀的子目录名
        posix_path = PurePosixPath(self.relative_path)
        namespaced_dir = f"{plugin_id.replace('.', '-')}-{posix_path.parts[0]}"
        # 第二步：拼接得到虚拟 POSIX 绝对路径
        virtual_path = str(PurePosixPath(skills_root, namespaced_dir, *posix_path.parts[1:]))
        logger.debug(
            "skill_virtual_path_computed",
            extra={"event_fields": {"plugin_id": plugin_id}},
        )
        return virtual_path

    def to_upload_payload(self, plugin_id: str, skills_root: str = "/skills") -> tuple[str, bytes]:
        """生成可直接传递给 Deep Agents Backend `upload_files` 的 (虚拟路径, UTF-8 字节流) 二元组。

        :param plugin_id: 所属插件的唯一标识符。
        :type plugin_id: str
        :param skills_root: 虚拟技能根目录路径，默认为 '/skills'。
        :type skills_root: str
        :return: (虚拟路径字符串, UTF-8 编码字节内容) 元组。
        :rtype: tuple[str, bytes]
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：解析目标虚拟路径
        vpath = self.to_virtual_path(plugin_id, skills_root=skills_root)
        # 第二步：将文本内容编码为 UTF-8 字节流
        encoded = self.content.encode("utf-8")
        logger.debug(
            "skill_upload_payload_generated",
            extra={"event_fields": {"plugin_id": plugin_id, "byte_size": len(encoded)}},
        )
        return (vpath, encoded)


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
        :rtype: str
        :author: tongliga
        :date: 2026-10-19
        """
        return self.tool.name

    @property
    def requires_approval(self) -> bool:
        """判断该工具是否需要启用人工审批中断（Human-in-the-Loop）。

        :return: 若工具具有写或外部副作用则返回 True。
        :rtype: bool
        :author: tongliga
        :date: 2026-10-19
        """
        return self.effect.requires_approval

    @property
    def mcp_annotations(self) -> dict[str, Any] | None:
        """读取 MCPAdapter 保留的服务端注解（snake_case），不将提示作为授权依据。

        :return: 注解信息字典，不存在时返回 None。
        :rtype: dict[str, typing.Any] | None
        :author: tongliga
        :date: 2026-10-19
        """
        value: Any = self.tool.metadata
        for key in ("mcp", "tool", "annotations"):
            if not isinstance(value, dict):
                return None
            value = value.get(key)
        return value if isinstance(value, dict) else None

    def to_interrupt_config(self) -> InterruptOnConfig:
        """构造 LangChain `HumanInTheLoopMiddleware` 原生兼容的审批中断配置字典。

        :return: 允许 approve 与 reject 决策的 InterruptOnConfig 配置。
        :rtype: langchain.agents.middleware.InterruptOnConfig
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：记录中断配置构建调试日志
        logger.debug(
            "tool_interrupt_config_created",
            extra={"event_fields": {"tool_name": self.name, "effect": self.effect.value}},
        )
        # 第二步：初始化支持 approve/reject 审批决策的配置字典
        config = InterruptOnConfig(allowed_decisions=["approve", "reject"])
        if annotations := self.mcp_annotations:
            config["description"] = (
                f"MCP tool execution requires approval\nTool: {self.name}\n"
                "Server-provided annotations (untrusted hints): "
                f"{json.dumps(annotations, ensure_ascii=False)}"
            )
        return config

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
        :type toolkit: langchain_core.tools.BaseToolkit
        :param effect: 批量应用的工具副作用分类，默认只读 READ。
        :type effect: vertical_plugins.common.contracts.SideEffect
        :param exposure: 批量应用的工具暴露模式，默认直接暴露 EAGER。
        :type exposure: vertical_plugins.common.contracts.Exposure
        :return: 包装后的 ToolContribution 实例列表。
        :rtype: list[vertical_plugins.common.contracts.ToolContribution]
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：调用工具包接口导出全部原生工具
        tools = toolkit.get_tools()
        logger.debug(
            "tools_from_toolkit_extracted",
            extra={"event_fields": {"tool_count": len(tools), "effect": effect.value}},
        )
        # 第二步：统一构造为 ToolContribution 实例列表
        return [cls(tool=t, effect=effect, exposure=exposure) for t in tools]


def _schema_has_messages_key(schema: type[Any]) -> bool:
    """检查给定的 TypedDict / BaseModel / 类型注解是否包含 Deep Agents 要求的 `messages` 键。

    :param schema: 待检测的模式类型或模型类。
    :type schema: type[typing.Any]
    :return: 若包含 messages 键或字段则返回 True，否则返回 False。
    :rtype: bool
    :author: tongliga
    :date: 2026-10-19
    """
    annotations = getattr(schema, "__annotations__", None)
    if isinstance(annotations, dict) and "messages" in annotations:
        return True
    model_fields = getattr(schema, "model_fields", None)
    if isinstance(model_fields, dict) and "messages" in model_fields:
        return True
    has_key = any(
        isinstance(getattr(base, "__annotations__", None), dict)
        and "messages" in base.__annotations__
        for base in getattr(schema, "__mro__", ())
    )
    logger.debug(
        "schema_has_messages_checked",
        extra={"event_fields": {"schema_name": getattr(schema, "__name__", str(schema)), "has_messages": has_key}},
    )
    return has_key


class WorkflowContribution(StrictModel):
    """LangGraph 工作流插件契约：原生适配 Deep Agents `CompiledSubAgent` 与 `AsyncSubAgent`。

    每个工作流插件必须明确声明三层数据契约：
    - `internal_state` (`InternalState`)：定义工作流内部使用的数据，禁止外部调用方或 LLM 直接写入；
    - `input_schema`：定义调用方需要提供的数据（兼容 `messages` 协议）；
    - `output_schema`：定义工作流最终返回的数据（兼容 `messages` 与 `structured_response`）。
    """

    # 工作流子任务唯一名称（对应 task / start_async_task 的 subagent_type）
    name: WorkflowNameStr
    # 供主智能体（Supervisor）规划与委派时参考的工作流能力说明
    description: Annotated[str, Field(min_length=4)]
    # InternalState：定义工作流内部节点流转使用的数据类型（不可为 None）
    internal_state: type[Any] | None = None
    # input_schema：定义调用方需要提供的数据类型（不可为 None）
    input_schema: type[Any] | None = None
    # output_schema：定义工作流最终返回的数据类型（不可为 None）
    output_schema: type[Any] | None = None
    # 本地预编译的 LangGraph 状态图或 Runnable（用于 CompiledSubAgent 模式）
    runnable: Runnable[Any, Any] | None = None
    # Agent Protocol 服务中注册的图标识符（用于 AsyncSubAgent 模式及 langgraph.json 导出）
    graph_id: Annotated[str, Field(min_length=1)] | None = None
    # 可选的 Agent Protocol 远端地址；省略时在 AsyncSubAgent 下使用本地 ASGI 通道
    url: str | None = None
    # 可选的 Agent Protocol 远端认证或追踪请求头
    headers: dict[str, str] | None = None
    # 显式指定的接入模式；为 None 时若提供 runnable 默认采用 compiled，否则采用 async
    mode: WorkflowMode | None = None
    # 子智能体上下文模式：isolated（默认仅接收任务描述）或 fork（继承父对话上下文快照）
    context_mode: SubAgentContextMode = "isolated"
    # LangGraph 检查点持久化同步策略，默认强制开启 durability="sync" 以保障 Time Travel
    durability: Durability = "sync"
    # 工作流整体副作用等级（WRITE / EXTERNAL 将在委派前触发 HITL 审批中断）
    effect: SideEffect = SideEffect.READ
    # 前置依赖的工作流名称元组（由 SupervisorPolicyMiddleware 强制校验执行先后顺序）
    depends_on: tuple[str, ...] = ()
    # 单轮用户请求允许的最大委派次数（防止 Supervisor 无限重试同一工作流）
    max_dispatches: Annotated[int, Field(ge=1, le=50)] = 5
    # 是否对完全相同的任务描述启用幂等结果复用
    idempotent: bool = False
    # 允许委派该工作流的租户 ID 白名单（None 表示对所有租户开放）
    allowed_tenants: frozenset[str] | None = None
    # 可选的工作流结构化输出 Pydantic 校验模型（用于 Supervisor Validate 阶段确定性验收）
    result_schema: type[BaseModel] | None = None

    @model_validator(mode="after")
    def _validate_workflow_spec(self) -> Self:
        """校验工作流插件声明的完整性、三层状态契约（InternalState/input_schema/output_schema）与模式一致性。

        :return: 校验通过的当前 WorkflowContribution 实例。
        :rtype: Self
        :raises ValueError: 当名称保留、缺少 runnable/graph_id、缺少三层状态模式或模式配置矛盾时抛出。
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：校验工作流名称非内置保留字，且至少配置 runnable 或 graph_id 之一
        if self.name in _RESERVED_SUBAGENT_NAMES:
            raise ValueError(f"Reserved workflow subagent name: {self.name}")
        if self.runnable is None and self.graph_id is None:
            raise ValueError(
                f"Workflow '{self.name}' must provide at least one of 'runnable' or 'graph_id'"
            )
        if self.mode == WorkflowMode.COMPILED and self.runnable is None:
            raise ValueError(f"Workflow '{self.name}' in 'compiled' mode requires 'runnable'")
        if self.mode == WorkflowMode.ASYNC and self.graph_id is None:
            raise ValueError(f"Workflow '{self.name}' in 'async' mode requires 'graph_id'")
        if (self.url is not None or self.headers is not None) and self.graph_id is None:
            raise ValueError(
                f"Workflow '{self.name}' cannot set 'url' or 'headers' without 'graph_id'"
            )
        if self.name in self.depends_on:
            raise ValueError(f"Workflow '{self.name}' cannot depend on itself")

        # 第二步：若传入了 CompiledStateGraph 且未显式指定三层 schema，从 StateGraph 提取并严格核对
        if isinstance(self.runnable, CompiledStateGraph):
            builder = self.runnable.builder
            if self.internal_state is None:
                self.internal_state = builder.state_schema
            elif builder.state_schema is not self.internal_state:
                raise ValueError(
                    f"Workflow '{self.name}' internal_state does not match StateGraph state_schema"
                )
            if self.input_schema is None:
                self.input_schema = builder.input_schema
            elif builder.input_schema is not self.input_schema:
                raise ValueError(
                    f"Workflow '{self.name}' input_schema does not match StateGraph input_schema"
                )
            if self.output_schema is None:
                self.output_schema = builder.output_schema
            elif builder.output_schema is not self.output_schema:
                raise ValueError(
                    f"Workflow '{self.name}' output_schema does not match StateGraph output_schema"
                )

        # 第三步：断言三层状态模式必须完全独立定义，严禁复用同一对象
        if self.internal_state is None or self.input_schema is None or self.output_schema is None:
            raise ValueError(
                f"Workflow '{self.name}' must define 'internal_state', 'input_schema', and 'output_schema'"
            )
        if self.internal_state is self.input_schema or self.internal_state is self.output_schema:
            raise ValueError(
                f"Workflow '{self.name}' must define distinct 'internal_state', 'input_schema', and 'output_schema'"
            )

        # 第四步：检查输入与输出 schema 是否包含 messages 契约键
        if not _schema_has_messages_key(self.input_schema):
            raise ValueError(
                f"Workflow '{self.name}' input_schema must include a 'messages' key for Deep Agents delegation"
            )
        if not _schema_has_messages_key(self.output_schema):
            raise ValueError(
                f"Workflow '{self.name}' output_schema must include a 'messages' key for Deep Agents delegation"
            )
        logger.debug(
            "workflow_spec_validated",
            extra={"event_fields": {"workflow_name": self.name, "mode": str(self.mode)}},
        )
        return self

    @property
    def effective_mode(self) -> WorkflowMode:
        """返回当前工作流默认生效的接入模式。

        :return: WorkflowMode.COMPILED 或 WorkflowMode.ASYNC。
        :rtype: vertical_plugins.common.contracts.WorkflowMode
        :author: tongliga
        :date: 2026-10-19
        """
        if self.mode is not None:
            return self.mode
        return WorkflowMode.COMPILED if self.runnable is not None else WorkflowMode.ASYNC

    @property
    def requires_approval(self) -> bool:
        """判断委派该工作流是否需要人工审批（Human-in-the-Loop）。

        :return: 若副作用为 WRITE 或 EXTERNAL 则返回 True。
        :rtype: bool
        :author: tongliga
        :date: 2026-10-19
        """
        return self.effect.requires_approval

    def resolve_mode(self, preferred_mode: WorkflowMode | str | None = None) -> WorkflowMode:
        """结合宿主偏好解析最终接入模式（若插件同时提供 runnable 与 graph_id 可按宿主策略切换）。

        :param preferred_mode: 宿主偏好的工作流模式（'compiled' 或 'async'）。
        :type preferred_mode: vertical_plugins.common.contracts.WorkflowMode | str | None
        :return: 最终解析出的 WorkflowMode。
        :rtype: vertical_plugins.common.contracts.WorkflowMode
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：优先尊重工作流显式声明的接入模式
        if self.mode is not None:
            resolved = self.mode
        elif preferred_mode == WorkflowMode.ASYNC and self.graph_id is not None:
            resolved = WorkflowMode.ASYNC
        elif preferred_mode == WorkflowMode.COMPILED and self.runnable is not None:
            resolved = WorkflowMode.COMPILED
        else:
            resolved = self.effective_mode
        # 第二步：记录解析模式调试日志并返回
        logger.debug(
            "workflow_mode_resolved",
            extra={"event_fields": {"workflow_name": self.name, "resolved_mode": resolved.value}},
        )
        return resolved

    def to_subagent_spec(
        self,
        *,
        preferred_mode: WorkflowMode | str | None = None,
        durability_override: Durability | None = None,
        enable_checkpointer: bool = True,
    ) -> CompiledSubAgent | AsyncSubAgent:
        """转换为 Deep Agents 原生支持的 `CompiledSubAgent` 或 `AsyncSubAgent` 规范字典。

        :param preferred_mode: 宿主全局偏好的接入模式（仅当插件未强制锁死 mode 且具备对应字段时生效）。
        :type preferred_mode: vertical_plugins.common.contracts.WorkflowMode | str | None
        :param durability_override: 可选覆盖的检查点同步策略（默认使用工作流自身的 durability="sync"）。
        :type durability_override: langgraph.types.Durability | None
        :param enable_checkpointer: 是否为本地 CompiledStateGraph 自动启用父检查点继承与 durability 绑定。
        :type enable_checkpointer: bool
        :return: 符合 Deep Agents `create_deep_agent(subagents=...)` 契约的子智能体规范。
        :rtype: deepagents.CompiledSubAgent | deepagents.AsyncSubAgent
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：根据偏好与具备能力解析目标运行模式
        target_mode = self.resolve_mode(preferred_mode)
        logger.debug(
            "workflow_to_subagent_spec_generating",
            extra={"event_fields": {"workflow_name": self.name, "mode": target_mode.value}},
        )
        # 第二步：若为异步模式，构造 AsyncSubAgent 规范字典
        if target_mode == WorkflowMode.ASYNC:
            assert self.graph_id is not None
            async_spec: AsyncSubAgent = {
                "name": self.name,
                "description": self.description,
                "graph_id": self.graph_id,
                "mode": self.context_mode,
            }
            if self.url is not None:
                async_spec["url"] = self.url
            if self.headers is not None:
                async_spec["headers"] = dict(self.headers)
            return async_spec

        # 第三步：若为预编译模式，绑定检查点持久化策略并构造 CompiledSubAgent
        assert self.runnable is not None
        target_runnable: Runnable[Any, Any] = self.runnable
        effective_durability = durability_override or self.durability
        # None inherits per-call checkpoints; True would reuse state across task calls.
        if (
            enable_checkpointer
            and isinstance(target_runnable, CompiledStateGraph)
            and target_runnable.checkpointer is not False
        ):
            target_runnable = RunnableBinding(
                bound=target_runnable,
                kwargs={"durability": effective_durability},
            )
        compiled_spec: CompiledSubAgent = {
            "name": self.name,
            "description": self.description,
            "runnable": target_runnable,
            "mode": self.context_mode,
        }
        return compiled_spec

    @classmethod
    def from_compiled_graph(
        cls,
        *,
        name: str,
        description: str,
        runnable: Runnable[Any, Any],
        internal_state: type[Any] | None = None,
        input_schema: type[Any] | None = None,
        output_schema: type[Any] | None = None,
        graph_id: str | None = None,
        context_mode: SubAgentContextMode = "isolated",
        durability: Durability = "sync",
        effect: SideEffect = SideEffect.READ,
        depends_on: tuple[str, ...] = (),
        max_dispatches: int = 5,
        idempotent: bool = False,
        allowed_tenants: frozenset[str] | None = None,
        result_schema: type[BaseModel] | None = None,
    ) -> WorkflowContribution:
        """从预编译 LangGraph 状态图构造 `CompiledSubAgent` 工作流插件贡献。

        :param name: 工作流唯一名称。
        :type name: str
        :param description: 工作流描述说明。
        :type description: str
        :param runnable: 预编译的 LangGraph 状态图或可运行对象。
        :type runnable: langchain_core.runnables.Runnable[typing.Any, typing.Any]
        :param internal_state: 内部流转状态模式。
        :type internal_state: type[typing.Any] | None
        :param input_schema: 输入参数数据模式。
        :type input_schema: type[typing.Any] | None
        :param output_schema: 输出结果数据模式。
        :type output_schema: type[typing.Any] | None
        :param graph_id: 可选的远程图标识符。
        :type graph_id: str | None
        :param context_mode: 上下文传递模式。
        :type context_mode: vertical_plugins.common.contracts.SubAgentContextMode
        :param durability: 检查点持久化级别。
        :type durability: langgraph.types.Durability
        :param effect: 工作流副作用等级。
        :type effect: vertical_plugins.common.contracts.SideEffect
        :param depends_on: 前置依赖的工作流列表。
        :type depends_on: tuple[str, ...]
        :param max_dispatches: 最大允许委派次数。
        :type max_dispatches: int
        :param idempotent: 是否启用结果幂等复用。
        :type idempotent: bool
        :param allowed_tenants: 允许调用的租户白名单。
        :type allowed_tenants: frozenset[str] | None
        :param result_schema: 结构化输出结果验证模型。
        :type result_schema: type[pydantic.BaseModel] | None
        :return: 构造完成的 WorkflowContribution 实例。
        :rtype: vertical_plugins.common.contracts.WorkflowContribution
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：记录构造预编译工作流贡献日志
        logger.debug(
            "workflow_from_compiled_graph_created",
            extra={"event_fields": {"workflow_name": name, "has_graph_id": graph_id is not None}},
        )
        # 第二步：实例化 WorkflowContribution
        return cls(
            name=name,
            description=description,
            internal_state=internal_state,
            input_schema=input_schema,
            output_schema=output_schema,
            runnable=runnable,
            graph_id=graph_id,
            mode=WorkflowMode.COMPILED if graph_id is None else None,
            context_mode=context_mode,
            durability=durability,
            effect=effect,
            depends_on=depends_on,
            max_dispatches=max_dispatches,
            idempotent=idempotent,
            allowed_tenants=allowed_tenants,
            result_schema=result_schema,
        )

    @classmethod
    def from_async_graph(
        cls,
        *,
        name: str,
        description: str,
        graph_id: str,
        internal_state: type[Any] = WorkflowInternalState,
        input_schema: type[Any] = WorkflowInputState,
        output_schema: type[Any] = WorkflowOutputState,
        url: str | None = None,
        headers: dict[str, str] | None = None,
        runnable: Runnable[Any, Any] | None = None,
        context_mode: SubAgentContextMode = "isolated",
        effect: SideEffect = SideEffect.READ,
        depends_on: tuple[str, ...] = (),
        max_dispatches: int = 5,
        idempotent: bool = False,
        allowed_tenants: frozenset[str] | None = None,
        result_schema: type[BaseModel] | None = None,
    ) -> WorkflowContribution:
        """从 Agent Protocol `graph_id` 构造 `AsyncSubAgent` 异步工作流插件贡献。

        :param name: 工作流唯一名称。
        :type name: str
        :param description: 工作流描述说明。
        :type description: str
        :param graph_id: 远程图标识符。
        :type graph_id: str
        :param internal_state: 内部流转状态模式。
        :type internal_state: type[typing.Any]
        :param input_schema: 输入参数数据模式。
        :type input_schema: type[typing.Any]
        :param output_schema: 输出结果数据模式。
        :type output_schema: type[typing.Any]
        :param url: 远程服务端地址。
        :type url: str | None
        :param headers: 远程服务请求头字典。
        :type headers: dict[str, str] | None
        :param runnable: 可选的本地备用可运行对象。
        :type runnable: langchain_core.runnables.Runnable[typing.Any, typing.Any] | None
        :param context_mode: 上下文传递模式。
        :type context_mode: vertical_plugins.common.contracts.SubAgentContextMode
        :param effect: 工作流副作用等级。
        :type effect: vertical_plugins.common.contracts.SideEffect
        :param depends_on: 前置依赖的工作流列表。
        :type depends_on: tuple[str, ...]
        :param max_dispatches: 最大允许委派次数。
        :type max_dispatches: int
        :param idempotent: 是否启用结果幂等复用。
        :type idempotent: bool
        :param allowed_tenants: 允许调用的租户白名单。
        :type allowed_tenants: frozenset[str] | None
        :param result_schema: 结构化输出结果验证模型。
        :type result_schema: type[pydantic.BaseModel] | None
        :return: 构造完成的 WorkflowContribution 实例。
        :rtype: vertical_plugins.common.contracts.WorkflowContribution
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：记录构造异步工作流贡献日志
        logger.debug(
            "workflow_from_async_graph_created",
            extra={"event_fields": {"workflow_name": name, "graph_id": graph_id}},
        )
        # 第二步：实例化 WorkflowContribution
        return cls(
            name=name,
            description=description,
            internal_state=internal_state,
            input_schema=input_schema,
            output_schema=output_schema,
            graph_id=graph_id,
            url=url,
            headers=headers,
            runnable=runnable,
            mode=WorkflowMode.ASYNC,
            context_mode=context_mode,
            effect=effect,
            depends_on=depends_on,
            max_dispatches=max_dispatches,
            idempotent=idempotent,
            allowed_tenants=allowed_tenants,
            result_schema=result_schema,
        )


class PluginContribution(StrictModel):
    """插件向宿主框架贡献的所有原生组件集合。"""

    # 插件贡献的 LangGraph 确定性工作流子任务列表（推荐主接入方式）
    workflows: list[WorkflowContribution] = Field(default_factory=list)
    # 插件贡献的原生工具列表（兼容轻量工具与技能门控工具）
    tools: list[ToolContribution] = Field(default_factory=list)
    # 插件贡献的技能资产文件列表
    skills: list[SkillAsset] = Field(default_factory=list)
    # 插件贡献的 LangChain / Deep Agents 中间件列表
    middleware: list[AgentMiddleware[Any, Any, Any]] = Field(default_factory=list)
    # 插件贡献的原始子智能体定义或预编译子智能体列表
    subagents: list[SubAgentSpec] = Field(default_factory=list)
    # 插件追加至主智能体的专用系统指令说明
    instructions: str = ""

    def format_system_instruction(self, plugin_id: str) -> str | None:
        """生成带有插件命名空间标识的系统提示词段落。

        :param plugin_id: 插件唯一标识符。
        :type plugin_id: str
        :return: 若配置了 instructions 则返回带前缀的指令字符串，否则返回 None。
        :rtype: str | None
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：委托公共提示词模块按统一前缀格式拼装
        return format_plugin_instruction(plugin_id, self.instructions)


@dataclass(frozen=True, slots=True)
class BuildServices:
    """宿主在构建期提供的无单例依赖服务集合。"""

    # 智能体运行的工作区绝对物理根目录
    workspace_root: Path
    # 可选的长期记忆及键值持久化存储 BaseStore 实例
    store: BaseStore | None = None
    plugin_config: dict[str, dict[str, Any]] = field(default_factory=dict, repr=False)
    python_executable: str = sys.executable

    def config_for(self, plugin_id: str) -> dict[str, Any]:
        """为插件本地模式验证返回隔离的配置副本。

        :param plugin_id: 启用的插件标识符。
        :type plugin_id: str
        :return: 插件专用配置字典，不存在时返回空字典。
        :rtype: dict[str, typing.Any]
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：记录配置获取调试日志
        logger.debug(
            "build_services_config_retrieved",
            extra={"event_fields": {"plugin_id": plugin_id}},
        )
        # 第二步：深拷贝插件专属配置避免外部副作用修改
        return deepcopy(self.plugin_config.get(plugin_id, {}))


@dataclass(frozen=True, slots=True)
class RunContext:
    """每次智能体运行由 LangGraph 注入的可信运行时上下文（非模型生成参数）。"""

    # 隔离租户标识符
    tenant_id: str
    # 隔离用户标识符
    user_id: str
    # 任务质量/预算层级：economy（经济型）或 quality（高品质型）
    task_tier: Literal["economy", "quality"] = "quality"

    def __post_init__(self) -> None:
        """校验可信边界处的关键租户与用户身份标识非空。

        :return: 无返回值。
        :rtype: None
        :raises ValueError: 当租户或用户标识为空，或任务层级非法时抛出。
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：记录初始化校验日志
        logger.debug(
            "run_context_initialized",
            extra={
                "event_fields": {
                    "task_tier": self.task_tier,
                    "has_tenant": bool(self.tenant_id),
                    "has_user": bool(self.user_id),
                }
            },
        )
        # 第二步：断言租户与用户标识非空
        if any(
            not isinstance(value, str) or not value.strip()
            for value in (self.tenant_id, self.user_id)
        ):
            raise ValueError("RunContext requires nonempty trusted tenant_id and user_id")
        # 第三步：断言任务层级属于 economy 或 quality
        if self.task_tier not in {"economy", "quality"}:
            raise ValueError("task_tier must be economy or quality")


__all__ = [
    "BuildServices",
    "Exposure",
    "PluginContribution",
    "PluginIdStr",
    "PluginManifest",
    "RunContext",
    "SemVerStr",
    "SideEffect",
    "SkillAsset",
    "StrictModel",
    "SubAgentContextMode",
    "SubAgentSpec",
    "ToolContribution",
    "WorkflowContribution",
    "WorkflowInputState",
    "WorkflowInternalState",
    "WorkflowMode",
    "WorkflowNameStr",
    "WorkflowOutputState",
]
