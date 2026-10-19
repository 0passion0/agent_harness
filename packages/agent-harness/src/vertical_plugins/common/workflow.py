"""声明式 LangGraph 工作流基类、节点计划、统一 Hook、运行实例与按需状态观察模块。"""

from __future__ import annotations

import inspect
import json
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Sequence
from copy import deepcopy
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal, Protocol, cast, runtime_checkable
from uuid import uuid4

from langchain_core.messages import AIMessage, AnyMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphBubbleUp
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.base import BaseStore
from langgraph.types import Command, Durability, StateSnapshot, interrupt
from pydantic import BaseModel, Field, model_validator

from vertical_plugins.common.contracts import (
    BuildServices,
    SideEffect,
    StrictModel,
    SubAgentContextMode,
    WorkflowContribution,
    WorkflowInputState,
    WorkflowInternalState,
    WorkflowMode,
    WorkflowOutputState,
)

logger = logging.getLogger(__name__)

# 默认受保护的内部状态字段集合，禁止外部调用方或主智能体直接篡改
DEFAULT_PROTECTED_STATE_FIELDS: frozenset[str] = frozenset(
    {
        "validated_input",
        "validation_error",
        "evaluation_result",
        "profile_result",
        "analysis_result",
        "audit_result",
        "report_result",
        "analysis_executions",
        "workflow_events",
        "structured_response",
    }
)


@runtime_checkable
class AuthorizationGuardProtocol(Protocol):
    """宿主注入的可信插件执行授权守卫协议（由 vertical_harness 实现并注入）。"""

    def verify_execution(
        self,
        *,
        plugin_id: str,
        workflow_name: str,
        user_id: str,
        tenant_id: str,
        thread_id: str,
        run_id: str,
        input_data: dict[str, Any] | None = None,
        effect: SideEffect = SideEffect.READ,
    ) -> None:
        """校验当前插件执行请求是否已获得可信宿主的用户授权。

        :param plugin_id: 插件唯一标识符。
        :type plugin_id: str
        :param workflow_name: 工作流名称。
        :type workflow_name: str
        :param user_id: 当前用户 ID。
        :type user_id: str
        :param tenant_id: 当前租户 ID。
        :type tenant_id: str
        :param thread_id: 会话线程 ID。
        :type thread_id: str
        :param run_id: 本次运行 ID。
        :type run_id: str
        :param input_data: 输入参数字典。
        :type input_data: dict[str, typing.Any] | None
        :param effect: 操作副作用级别。
        :type effect: vertical_plugins.common.contracts.SideEffect
        :return: 校验通过时无返回值；未授权时抛出 PermissionError。
        :rtype: None
        :raises PermissionError: 当插件未经用户授权、超出批准范围或授权已撤销时抛出。
        :author: tongliga
        :date: 2026-10-19
        """


@dataclass(frozen=True, slots=True)
class WorkflowContext:
    """工作流运行所需的可信宿主依赖上下文（工作区、模型、会话、存储与授权守卫）。"""

    workspace_root: Path = field(default_factory=lambda: Path("./.agent-workspace").resolve())
    model: Any = None
    store: BaseStore | None = None
    checkpointer: BaseCheckpointSaver[Any] | bool | None = None
    thread_id: str | None = None
    run_id: str | None = None
    tenant_id: str = "default"
    user_id: str = "default"
    plugin_config: dict[str, Any] = field(default_factory=dict)
    auth_guard: AuthorizationGuardProtocol | None = None

    def __post_init__(self) -> None:
        """规范化工作区物理根路径。

        :return: 无返回值。
        :rtype: None
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：将工作区根目录解析为绝对路径
        object.__setattr__(self, "workspace_root", Path(self.workspace_root).expanduser().resolve())

    @classmethod
    def from_services(
        cls,
        services: BuildServices | None = None,
        *,
        plugin_id: str = "",
        model: Any = None,
        checkpointer: BaseCheckpointSaver[Any] | bool | None = None,
        auth_guard: AuthorizationGuardProtocol | None = None,
    ) -> WorkflowContext:
        """从宿主 BuildServices 构造隔离的 WorkflowContext。

        :param services: 宿主构建服务实例。
        :type services: vertical_plugins.common.contracts.BuildServices | None
        :param plugin_id: 插件唯一标识符。
        :type plugin_id: str
        :param model: 可选的语言模型实例。
        :type model: typing.Any
        :param checkpointer: 可选的检查点保存器。
        :type checkpointer: langgraph.checkpoint.base.BaseCheckpointSaver[typing.Any] | bool | None
        :param auth_guard: 可选的宿主授权守卫实例。
        :type auth_guard: AuthorizationGuardProtocol | None
        :return: 构造完成的 WorkflowContext 对象。
        :rtype: WorkflowContext
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：若未提供 services 则采用默认工作区构造
        if services is None:
            return cls(model=model, checkpointer=checkpointer, auth_guard=auth_guard)
        # 第二步：提取插件专属配置并注入服务依赖
        cfg = services.config_for(plugin_id) if plugin_id else {}
        return cls(
            workspace_root=services.workspace_root,
            model=model or cfg.get("model"),
            store=services.store,
            checkpointer=checkpointer,
            plugin_config=cfg,
            auth_guard=auth_guard,
        )

    def with_overrides(self, **overrides: Any) -> WorkflowContext:
        """返回覆盖指定字段后的新 ``WorkflowContext`` 实例。

        :param overrides: 待覆盖的上下文字段键值对。
        :type overrides: typing.Any
        :return: 覆盖后的新上下文对象。
        :rtype: WorkflowContext
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：过滤出屬於 WorkflowContext 的合法字段并执行不可变替换
        valid_fields = {f_name for f_name in self.__dataclass_fields__ if f_name in overrides}
        filtered = {k: overrides[k] for k in valid_fields}
        return replace(self, **filtered)

    def resolve_workspace_path(self, raw_path: str | Path) -> Path:
        """将输入路径解析为严格受限于工作区根目录内的绝对物理路径。

        :param raw_path: 原始相对路径、虚拟路径（如 `/workspace/data.csv`）或工作区内绝对路径。
        :type raw_path: str | pathlib.Path
        :return: 校验通过的工作区内绝对物理路径。
        :rtype: pathlib.Path
        :raises ValueError: 当路径为空或试图越界访问工作区外部目录时抛出。
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：清理虚拟前缀并检查空路径
        text = str(raw_path).strip()
        if not text:
            raise ValueError("Workspace path must not be empty")
        if text.startswith("/workspace/"):
            text = text.removeprefix("/workspace/").lstrip("/")
        candidate = Path(text)

        # 第二步：计算目标物理绝对路径
        root = self.workspace_root.resolve()
        resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()

        # 第三步：严格验证目标路径位于工作区根目录内部，阻断目录遍历越界
        if not resolved.is_relative_to(root):
            raise ValueError(f"Path escapes workspace boundary ({root}): {raw_path}")
        return resolved


@dataclass(frozen=True, slots=True)
class WorkflowStep:
    """声明式工作流单个业务节点定义。"""

    name: str
    handler: Callable[..., dict[str, Any]]
    editable_fields: tuple[str, ...] = ()
    input_model: type[BaseModel] | None = None
    retry_limit: int = 0
    is_publish: bool = False
    description: str = ""

    def __post_init__(self) -> None:
        """校验节点名称与重试上限参数合法性。

        :return: 无返回值。
        :rtype: None
        :raises ValueError: 当节点名称为空或 retry_limit 为负数时抛出。
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：校验节点名非空且非内部保留标识
        if not self.name or not self.name.strip() or self.name.startswith("__"):
            raise ValueError(f"Invalid workflow step name: {self.name!r}")
        # 第二步：校验重试次数非负
        if self.retry_limit < 0:
            raise ValueError("retry_limit must be nonnegative")


class WorkflowPlan(StrictModel):
    """声明式的固定工作流节点序列与拓扑计划。"""

    steps: tuple[WorkflowStep, ...]

    @model_validator(mode="after")
    def _validate_steps(self) -> WorkflowPlan:
        """校验工作流计划非空且节点名称全局唯一。

        :return: 校验通过的 WorkflowPlan。
        :rtype: WorkflowPlan
        :raises ValueError: 当节点列表为空或存在重名节点时抛出。
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：断言至少包含一个业务节点
        if not self.steps:
            raise ValueError("WorkflowPlan requires at least one step")
        # 第二步：核验节点名称不重复
        seen: set[str] = set()
        for step in self.steps:
            if step.name in seen:
                raise ValueError(f"Duplicate workflow step name: {step.name}")
            seen.add(step.name)
        return self

    @property
    def node_names(self) -> tuple[str, ...]:
        """按声明顺序返回全部固定节点名称元组。

        :return: 节点名称元组。
        :rtype: tuple[str, ...]
        :author: tongliga
        :date: 2026-10-19
        """
        return tuple(step.name for step in self.steps)

    def get_step(self, node_name: str) -> WorkflowStep:
        """按名称查找工作流节点定义。

        :param node_name: 目标节点名称。
        :type node_name: str
        :return: 匹配的 WorkflowStep。
        :rtype: WorkflowStep
        :raises ValueError: 当节点名称不在固定工作流计划中时抛出。
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：线性匹配固定节点计划
        for step in self.steps:
            if step.name == node_name:
                return step
        raise ValueError(
            f"Unknown workflow node {node_name!r}. Valid nodes: {list(self.node_names)}"
        )


class WorkflowNodeEvent(StrictModel):
    """工作流单节点执行的结构化事件摘要（不含原始大表或完整内部状态）。"""

    run_id: str
    plugin_id: str
    node_name: str
    status: Literal["running", "completed", "failed", "interrupted", "skipped"]
    summary: str
    artifact_refs: list[str] = Field(default_factory=list)


class WorkflowProgressSummary(StrictModel):
    """按需查询工作流运行进度时返回的轻量级状态模型。"""

    run_id: str
    plugin_id: str
    thread_id: str
    current_node: str | None = None
    completed_nodes: list[str] = Field(default_factory=list)
    total_nodes: int
    progress_ratio: float
    status: Literal["pending", "running", "interrupted", "completed", "failed"]
    error_summary: str | None = None


class NodeObservation(StrictModel):
    """按权限查询单个工作流节点时返回的输入输出脱敏摘要模型。"""

    run_id: str
    plugin_id: str
    node_name: str
    status: str
    input_summary: dict[str, Any] = Field(default_factory=dict)
    output_summary: dict[str, Any] = Field(default_factory=dict)
    artifact_refs: list[str] = Field(default_factory=list)


class HookAuditEntry(StrictModel):
    """工作流 Hook 与节点干预操作的不可变审计记录项。"""

    thread_id: str
    node_name: str
    action: Literal[
        "before_run",
        "before_node",
        "after_node",
        "on_error",
        "before_publish",
        "after_run",
        "modify_input",
        "retry",
        "fork",
    ]
    fields_modified: tuple[str, ...] = ()
    checkpoint_id: str | None = None
    detail: str = ""


@dataclass(slots=True)
class WorkflowHooks:
    """用户可选的工作流生命周期 Hook 与节点暂停审阅配置。"""

    pause_before_nodes: frozenset[str] = field(default_factory=frozenset)
    pause_before_publish: bool = False
    pause_on_error: bool = False
    before_run: Callable[[dict[str, Any], WorkflowContext], dict[str, Any] | None] | None = None
    before_node: Callable[[str, dict[str, Any], WorkflowContext], dict[str, Any] | None] | None = (
        None
    )
    after_node: Callable[[WorkflowNodeEvent, WorkflowContext], None] | None = None
    on_error: (
        Callable[
            [str, Exception, int, WorkflowContext],
            Literal["retry", "cancel", "raise"] | dict[str, Any],
        ]
        | None
    ) = None
    before_publish: Callable[[dict[str, Any], WorkflowContext], bool | dict[str, Any]] | None = None
    after_run: Callable[[dict[str, Any], WorkflowContext], None] | None = None


def _summarize_dict(data: dict[str, Any], max_str_len: int = 160) -> dict[str, Any]:
    """将状态或增量字典压缩为脱敏摘要，避免传输大型表格或长文本。

    :param data: 原始字典。
    :type data: dict[str, typing.Any]
    :param max_str_len: 单字段字符串最大保留长度。
    :type max_str_len: int
    :return: 压缩后的摘要字典。
    :rtype: dict[str, typing.Any]
    :author: tongliga
    :date: 2026-10-19
    """
    # 第一步：遍历键值对并对长字符串、大列表和嵌套字典做有界截断
    summary: dict[str, Any] = {}
    for key, value in data.items():
        if key == "messages":
            summary["message_count"] = len(value) if isinstance(value, list) else 1
            continue
        if isinstance(value, str):
            summary[key] = value if len(value) <= max_str_len else f"{value[:max_str_len]}...(len={len(value)})"
        elif isinstance(value, (int, float, bool)) or value is None:
            summary[key] = value
        elif isinstance(value, list):
            summary[key] = f"list[len={len(value)}]"
        elif isinstance(value, dict):
            summary[key] = {
                k: (
                    v
                    if isinstance(v, (int, float, bool)) or v is None
                    else (v[:80] if isinstance(v, str) else type(v).__name__)
                )
                for k, v in list(value.items())[:8]
            }
        else:
            summary[key] = type(value).__name__
    return summary


def _extract_artifact_refs(update: dict[str, Any]) -> list[str]:
    """从节点输出增量字典中提取产物相对路径引用列表。

    :param update: 节点返回的更新字典。
    :type update: dict[str, typing.Any]
    :return: 产物路径字符串列表。
    :rtype: list[str]
    :author: tongliga
    :date: 2026-10-19
    """
    # 第一步：扫描常见产物路径字段并去重收集
    refs: list[str] = []
    profile = update.get("profile_result")
    if isinstance(profile, dict) and isinstance(profile.get("profile_file"), str):
        refs.append(profile["profile_file"])
    executions = update.get("analysis_executions")
    if isinstance(executions, list):
        for item in executions:
            if isinstance(item, dict):
                if isinstance(item.get("script"), str):
                    refs.append(item["script"])
                art_map = item.get("artifact_sha256")
                if isinstance(art_map, dict):
                    refs.extend(str(k) for k in art_map)
    report = update.get("report_result")
    if isinstance(report, dict) and isinstance(report.get("report_path"), str):
        refs.append(report["report_path"])
    return list(dict.fromkeys(refs))


class BaseWorkflow(ABC):
    """统一管理声明式 LangGraph 状态图构建、缓存、Checkpointer、Hook 与执行的抽象基类。"""

    plugin_id: str = "plugin"
    workflow_name: str = "workflow"
    description: str = "Declarative LangGraph workflow"
    input_schema: type[Any] = WorkflowInputState
    output_schema: type[Any] = WorkflowOutputState
    internal_state: type[Any] = WorkflowInternalState
    effect: SideEffect = SideEffect.READ
    idempotent: bool = False
    max_dispatches: int = 5
    result_schema: type[BaseModel] | None = None
    protected_fields: frozenset[str] = DEFAULT_PROTECTED_STATE_FIELDS

    def __init__(
        self,
        context: WorkflowContext | None = None,
        *,
        workspace_root: Path | None = None,
        checkpointer: BaseCheckpointSaver[Any] | bool | None = None,
        model: Any = None,
        hooks: WorkflowHooks | None = None,
    ) -> None:
        """初始化声明式工作流实例（延迟编译，不在初始化时立即构建状态图）。

        :param context: 可选的可信运行上下文。
        :type context: WorkflowContext | None
        :param workspace_root: 可选覆盖的工作区根目录。
        :type workspace_root: pathlib.Path | None
        :param checkpointer: 可选覆盖的检查点保存器。
        :type checkpointer: langgraph.checkpoint.base.BaseCheckpointSaver[typing.Any] | bool | None
        :param model: 可选绑定的语言模型。
        :type model: typing.Any
        :param hooks: 可选的用户生命周期 Hook 配置。
        :type hooks: WorkflowHooks | None
        :return: 无返回值。
        :rtype: None
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：合并上下文参数并规范化工作区根路径
        base_ctx = context or WorkflowContext()
        effective_root = (
            Path(workspace_root).expanduser().resolve()
            if workspace_root is not None
            else base_ctx.workspace_root
        )
        effective_checkpointer = (
            checkpointer if checkpointer is not None else base_ctx.checkpointer
        )
        effective_model = model if model is not None else base_ctx.model
        self.context = WorkflowContext(
            workspace_root=effective_root,
            model=effective_model,
            store=base_ctx.store,
            checkpointer=effective_checkpointer,
            thread_id=base_ctx.thread_id,
            run_id=base_ctx.run_id,
            tenant_id=base_ctx.tenant_id,
            user_id=base_ctx.user_id,
            plugin_config=dict(base_ctx.plugin_config),
            auth_guard=base_ctx.auth_guard,
        )
        # 第二步：保存兼容属性与延迟编译缓存槽位
        self.workspace_root = self.context.workspace_root
        self.checkpointer = self.context.checkpointer
        self.model = self.context.model
        self.hooks = hooks or WorkflowHooks()
        self._plan: WorkflowPlan | None = None
        self._compiled_graph: CompiledStateGraph[Any, Any, Any, Any] | None = None
        self._events_by_thread: dict[str, list[WorkflowNodeEvent]] = {}
        self._node_observations: dict[tuple[str, str], NodeObservation] = {}
        self._audit_log: list[HookAuditEntry] = []

    @abstractmethod
    def run(self) -> WorkflowPlan:
        """声明固定工作流的全部业务阶段及其执行顺序。

        子类必须直接在 ``run()`` 内通过 ``self.pipeline(self.step(...), ...)``
        清晰列出所有业务执行阶段。

        :return: 固定工作流计划对象。
        :rtype: WorkflowPlan
        :author: tongliga
        :date: 2026-10-19
        """

    def step(
        self,
        name: str,
        handler: Callable[..., dict[str, Any]],
        *,
        editable_fields: Iterable[str] = (),
        input_model: type[BaseModel] | None = None,
        retry_limit: int = 0,
        is_publish: bool = False,
        description: str = "",
    ) -> WorkflowStep:
        """声明单个工作流节点步骤。

        :param name: 节点唯一名称。
        :type name: str
        :param handler: 节点业务处理函数。
        :type handler: collections.abc.Callable[..., dict[str, typing.Any]]
        :param editable_fields: 允许在 Hook 中修改的输入字段白名单。
        :type editable_fields: collections.abc.Iterable[str]
        :param input_model: 可选的节点输入修改 Pydantic 校验模型。
        :type input_model: type[pydantic.BaseModel] | None
        :param retry_limit: 节点异常时的最大有限重试次数。
        :type retry_limit: int
        :param is_publish: 是否为最终报告/产物发布节点（触发 before_publish Hook）。
        :type is_publish: bool
        :param description: 节点功能简述。
        :type description: str
        :return: 构建的 WorkflowStep 实例。
        :rtype: WorkflowStep
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：组装并返回强类型 WorkflowStep 定义
        return WorkflowStep(
            name=name,
            handler=handler,
            editable_fields=tuple(editable_fields),
            input_model=input_model,
            retry_limit=retry_limit,
            is_publish=is_publish,
            description=description,
        )

    def pipeline(self, *steps: WorkflowStep) -> WorkflowPlan:
        """将按顺序排列的多个 WorkflowStep 组合为固定工作流计划。

        :param steps: 按执行顺序排列的节点步骤。
        :type steps: WorkflowStep
        :return: 经校验的 WorkflowPlan 实例。
        :rtype: WorkflowPlan
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：封装并返回不可变的 WorkflowPlan
        return WorkflowPlan(steps=tuple(steps))

    @property
    def plan(self) -> WorkflowPlan:
        """获取并缓存当前工作流声明的固定拓扑计划。

        :return: 固定工作流计划对象。
        :rtype: WorkflowPlan
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：首次访问时调用 run() 解析声明式计划并缓存
        if self._plan is None:
            self._plan = self.run()
        return self._plan

    def resolve_workspace(self, config: RunnableConfig | None = None) -> Path:
        """从运行时配置或工作流上下文获取工作区物理根目录。

        :param config: 可选的 LangChain 运行配置。
        :type config: langchain_core.runnables.RunnableConfig | None
        :return: 工作区绝对物理路径。
        :rtype: pathlib.Path
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：始终优先使用宿主在初始化上下文中注入的可信工作区根目录
        return self.context.workspace_root

    def resolve_model(self, config: RunnableConfig | None = None) -> Any:
        """从运行时配置或工作流上下文解析当前可用语言模型。

        :param config: 可选的 LangChain 运行配置。
        :type config: langchain_core.runnables.RunnableConfig | None
        :return: 语言模型实例或 None。
        :rtype: typing.Any
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：优先检查 configurable 中的模型注入，否则使用实例模型
        if config and isinstance(config, dict):
            cfg = config.get("configurable", {})
            if cfg.get("model") is not None:
                return cfg["model"]
        return self.model

    def validate_node_modification(
        self,
        node_name: str,
        values: dict[str, Any],
    ) -> dict[str, Any]:
        """校验针对指定节点的输入修改请求是否符合节点名、字段白名单与 Pydantic 契约。

        :param node_name: 目标节点名称。
        :type node_name: str
        :param values: 拟修改的字段字典。
        :type values: dict[str, typing.Any]
        :return: 校验通过的修改字典。
        :rtype: dict[str, typing.Any]
        :raises ValueError: 当节点不存在、修改字典为空、触碰受保护内部状态或超出可编辑白名单时抛出。
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：验证目标节点存在于固定计划中且修改载荷非空
        step = self.plan.get_step(node_name)
        if not isinstance(values, dict) or not values:
            raise ValueError("Node input modification requires a nonempty dict")

        # 第二步：拦截对受保护 InternalState 内部结果字段的篡改
        forbidden = set(values.keys()) & self.protected_fields
        if forbidden:
            raise ValueError(
                f"Cannot modify protected InternalState fields: {sorted(forbidden)}"
            )

        # 第三步：检查字段是否全部在当前节点的 editable_fields 白名单内
        allowed = set(step.editable_fields)
        disallowed = set(values.keys()) - allowed
        if disallowed:
            raise ValueError(
                f"Fields {sorted(disallowed)} are not editable on node {node_name!r}. "
                f"Editable fields: {sorted(allowed)}"
            )

        # 第四步：若节点配置了 Pydantic 输入校验模型，执行严格类型验证
        if step.input_model is not None:
            validated_model = step.input_model.model_validate(values)
            return validated_model.model_dump(exclude_unset=True)
        return deepcopy(values)

    def _invoke_step_handler(
        self,
        step: WorkflowStep,
        state: dict[str, Any],
        config: RunnableConfig,
    ) -> dict[str, Any]:
        """根据节点处理函数签名自适应传入 (state, config) 或 (state)。

        :param step: 工作流步骤定义。
        :type step: WorkflowStep
        :param state: 当前节点状态字典。
        :type state: dict[str, typing.Any]
        :param config: LangGraph 运行时配置。
        :type config: langchain_core.runnables.RunnableConfig
        :return: 节点执行返回的状态更新字典。
        :rtype: dict[str, typing.Any]
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：检查处理函数参数个数以决定是否传递 config
        sig = inspect.signature(step.handler)
        params = [
            p
            for p in sig.parameters.values()
            if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        ]
        if len(params) >= 2:
            return step.handler(state, config)
        return step.handler(state)

    def format_error_output(self, error_message: str, status: str = "invalid_input") -> dict[str, Any]:
        """将校验或运行错误格式化为符合 output_schema 的标准输出字典。

        :param error_message: 错误详情描述。
        :type error_message: str
        :param status: 错误状态码（默认 'invalid_input'）。
        :type status: str
        :return: 包含 messages 与 structured_response 的字典。
        :rtype: dict[str, typing.Any]
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：组装标准错误载荷与 AIMessage
        payload: dict[str, Any] = {
            "status": status,
            "error": error_message,
        }
        return {
            "messages": [AIMessage(content=json.dumps(payload, ensure_ascii=False))],
            "structured_response": payload,
        }

    def summarize_node_output(
        self,
        node_name: str,
        state: dict[str, Any],
        update: dict[str, Any],
    ) -> tuple[str, dict[str, Any], tuple[str, ...]]:
        """将节点输出提炼为简短状态说明、精简摘要字典与产物引用列表（不暴露大表数据）。

        :param node_name: 节点名称。
        :type node_name: str
        :param state: 节点输入状态字典。
        :type state: dict[str, typing.Any]
        :param update: 节点输出更新字典。
        :type update: dict[str, typing.Any]
        :return: (摘要文本, 输出摘要字典, 产物路径元组)。
        :rtype: tuple[str, dict[str, typing.Any], tuple[str, ...]]
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：默认提取精简摘要与产物引用
        status = "failed" if update.get("validation_error") else "completed"
        summary = (
            str(update.get("validation_error"))
            if update.get("validation_error")
            else f"Node {node_name} {status}"
        )
        return summary, _summarize_dict(update), tuple(_extract_artifact_refs(update))

    def _make_node_runner(
        self,
        step: WorkflowStep,
        step_index: int,
        total_steps: int,
    ) -> Callable[[dict[str, Any], RunnableConfig], dict[str, Any]]:
        """为单个 WorkflowStep 生成带 Hook、重试、事件记录与错误收敛的独立 LangGraph 节点函数。

        :param step: 当前节点步骤声明。
        :type step: WorkflowStep
        :param step_index: 节点在工作流中的序号（从 0 开始）。
        :type step_index: int
        :param total_steps: 工作流总节点数。
        :type total_steps: int
        :return: 可注册到 StateGraph 的节点可执行函数。
        :rtype: collections.abc.Callable[[dict[str, typing.Any], langchain_core.runnables.RunnableConfig], dict[str, typing.Any]]
        :author: tongliga
        :date: 2026-10-19
        """
        is_first = step_index == 0
        is_last = step_index == total_steps - 1

        def _node_fn(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
            # 第一步：解析当前会话线程 ID 与运行 ID
            configurable = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
            thread_id = str(configurable.get("thread_id") or self.context.thread_id or "default")
            run_id = str(
                (config or {}).get("run_id")
                or configurable.get("run_id")
                or self.context.run_id
                or uuid4().hex
            )
            active_state = dict(state)
            if is_first:
                self._events_by_thread[thread_id] = []

            # 第二步：首节点执行 before_run Hook（含宿主授权校验与输入确认）
            if is_first:
                if self.context.auth_guard is not None:
                    self.context.auth_guard.verify_execution(
                        plugin_id=self.plugin_id,
                        workflow_name=self.workflow_name,
                        user_id=str(configurable.get("user_id") or self.context.user_id),
                        tenant_id=str(configurable.get("tenant_id") or self.context.tenant_id),
                        thread_id=thread_id,
                        run_id=run_id,
                        input_data=active_state,
                        effect=self.effect,
                    )
                if self.hooks.before_run is not None:
                    hook_updates = self.hooks.before_run(active_state, self.context)
                    self._audit_log.append(
                        HookAuditEntry(
                            thread_id=thread_id,
                            node_name=step.name,
                            action="before_run",
                            fields_modified=tuple(hook_updates.keys()) if hook_updates else (),
                        )
                    )
                    if isinstance(hook_updates, dict):
                        active_state.update(hook_updates)

            # 第三步：若前置节点已标记校验错误或已提前完成交付，跳过中间计算并在末节点输出响应
            if not is_first and (
                active_state.get("validation_error") is not None
                or active_state.get("workflow_halted") is True
            ):
                skip_event = WorkflowNodeEvent(
                    run_id=run_id,
                    plugin_id=self.plugin_id,
                    node_name=step.name,
                    status="skipped",
                    summary=f"Skipped {step.name} due to prior halt or validation error",
                )
                self._events_by_thread.setdefault(thread_id, []).append(skip_event)
                if is_last and active_state.get("structured_response") is None:
                    err_text = str(active_state.get("validation_error") or "Workflow halted")
                    err_status = str(active_state.get("error_status") or "invalid_input")
                    return self.format_error_output(err_text, status=err_status)
                return {}

            # 第四步：执行 before_node Hook（支持按配置触发 LangGraph interrupt 暂停审阅或修改节点输入）
            cfg_pause_nodes = set(configurable.get("pause_before_nodes") or ())
            should_pause_node = (
                step.name in self.hooks.pause_before_nodes or step.name in cfg_pause_nodes
            )
            if should_pause_node:
                editable_preview = {
                    k: active_state.get(k) for k in step.editable_fields if k in active_state
                }
                resume_val = interrupt(
                    {
                        "hook": "before_node",
                        "plugin_id": self.plugin_id,
                        "node_name": step.name,
                        "editable_fields": list(step.editable_fields),
                        "current_inputs": _summarize_dict(editable_preview),
                    }
                )
                if isinstance(resume_val, dict):
                    raw_mod = resume_val.get("updates", resume_val)
                    if isinstance(raw_mod, dict) and raw_mod:
                        validated_mod = self.validate_node_modification(step.name, raw_mod)
                        active_state.update(validated_mod)
                        if isinstance(active_state.get("validated_input"), dict):
                            merged_valid = dict(active_state["validated_input"])
                            merged_valid.update(validated_mod)
                            active_state["validated_input"] = merged_valid
                        self._audit_log.append(
                            HookAuditEntry(
                                thread_id=thread_id,
                                node_name=step.name,
                                action="before_node",
                                fields_modified=tuple(validated_mod.keys()),
                                detail="Resumed from before_node interrupt with validated updates",
                            )
                        )

            if self.hooks.before_node is not None:
                cb_updates = self.hooks.before_node(step.name, active_state, self.context)
                if isinstance(cb_updates, dict) and cb_updates:
                    validated_cb = self.validate_node_modification(step.name, cb_updates)
                    active_state.update(validated_cb)
                    if isinstance(active_state.get("validated_input"), dict):
                        merged_valid = dict(active_state["validated_input"])
                        merged_valid.update(validated_cb)
                        active_state["validated_input"] = merged_valid
                    self._audit_log.append(
                        HookAuditEntry(
                            thread_id=thread_id,
                            node_name=step.name,
                            action="before_node",
                            fields_modified=tuple(validated_cb.keys()),
                            detail="Applied before_node callback updates",
                        )
                    )

            # 第五步：若当前节点为发布节点（is_publish=True），执行 before_publish Hook 审阅报告
            should_pause_publish = step.is_publish and (
                self.hooks.pause_before_publish
                or bool(configurable.get("pause_before_publish"))
            )
            if should_pause_publish:
                report_draft = active_state.get("markdown_report") or (
                    (active_state.get("validated_input") or {}).get("markdown_report")
                )
                pub_decision = interrupt(
                    {
                        "hook": "before_publish",
                        "plugin_id": self.plugin_id,
                        "node_name": step.name,
                        "report_preview": str(report_draft or "")[:500],
                    }
                )
                self._audit_log.append(
                    HookAuditEntry(
                        thread_id=thread_id,
                        node_name=step.name,
                        action="before_publish",
                        detail=f"User decision: {pub_decision!r}",
                    )
                )
                if pub_decision is False or (
                    isinstance(pub_decision, dict) and pub_decision.get("approved") is False
                ):
                    err = "Report publication rejected by user in before_publish hook"
                    return {
                        "validation_error": err,
                        "error_status": "failed",
                        **self.format_error_output(err, status="failed"),
                    }
                if isinstance(pub_decision, dict) and "markdown_report" in pub_decision:
                    validated_pub = self.validate_node_modification(
                        step.name, {"markdown_report": pub_decision["markdown_report"]}
                    )
                    active_state.update(validated_pub)

            if step.is_publish and self.hooks.before_publish is not None:
                pub_res = self.hooks.before_publish(active_state, self.context)
                self._audit_log.append(
                    HookAuditEntry(
                        thread_id=thread_id,
                        node_name=step.name,
                        action="before_publish",
                        detail="Executed before_publish callback",
                    )
                )
                if pub_res is False:
                    err = "Report publication rejected by before_publish hook"
                    return {
                        "validation_error": err,
                        "error_status": "failed",
                        **self.format_error_output(err, status="failed"),
                    }
                if isinstance(pub_res, dict) and pub_res:
                    validated_pub = self.validate_node_modification(step.name, pub_res)
                    active_state.update(validated_pub)

            # 第六步：执行节点处理函数，包含有限次数重试与 on_error Hook
            attempt = 0
            max_attempts = step.retry_limit + 1
            update: dict[str, Any] = {}
            while True:
                attempt += 1
                try:
                    update = self._invoke_step_handler(step, active_state, config)
                    break
                except GraphBubbleUp:
                    # LangGraph 原生 interrupt() 异常必须直接向上冒泡，不得作为错误拦截
                    raise
                except Exception as exc:
                    logger.warning(
                        "workflow_node_error",
                        extra={
                            "event_fields": {
                                "plugin_id": self.plugin_id,
                                "node_name": step.name,
                                "attempt": attempt,
                                "error_type": type(exc).__name__,
                            }
                        },
                    )
                    self._audit_log.append(
                        HookAuditEntry(
                            thread_id=thread_id,
                            node_name=step.name,
                            action="on_error",
                            detail=f"attempt={attempt}, error={type(exc).__name__}: {exc}",
                        )
                    )
                    if self.hooks.on_error is not None:
                        decision = self.hooks.on_error(step.name, exc, attempt, self.context)
                        if isinstance(decision, dict):
                            validated_fix = self.validate_node_modification(step.name, decision)
                            active_state.update(validated_fix)
                            if attempt < max_attempts:
                                continue
                        elif decision == "retry" and attempt < max_attempts:
                            continue
                        elif decision == "cancel":
                            err_msg = f"Node {step.name!r} cancelled after error: {exc}"
                            update = {
                                "validation_error": err_msg,
                                "error_status": "failed",
                                **(self.format_error_output(err_msg, status="failed") if is_last else {}),
                            }
                            break
                    if attempt < max_attempts:
                        continue
                    err_msg = f"Node {step.name!r} failed: {exc}"
                    update = {
                        "validation_error": err_msg,
                        "error_status": "failed",
                        **(self.format_error_output(err_msg, status="failed") if is_last else {}),
                    }
                    break

            # 第七步：记录 after_node 结构化观察事件与节点输入输出摘要（不暴露原始大表）
            node_failed = (
                update.get("validation_error") is not None
                or (
                    isinstance(update.get("report_result"), dict)
                    and update["report_result"].get("status") == "failed"
                )
                or (
                    isinstance(update.get("audit_result"), dict)
                    and update["audit_result"].get("status") == "failed"
                )
            )
            node_status: Literal["completed", "failed"] = "failed" if node_failed else "completed"
            artifact_refs = _extract_artifact_refs(update)
            summary_text = (
                str(update.get("validation_error"))
                if update.get("validation_error")
                else f"Node {step.name} {node_status}"
            )
            custom_summary = self.summarize_node_output(step.name, active_state, update)
            if custom_summary is not None:
                c_summary, c_out_summary, c_refs = custom_summary
                if not update.get("validation_error") and c_summary:
                    summary_text = c_summary
                if c_refs:
                    artifact_refs = list(dict.fromkeys((*artifact_refs, *c_refs)))
                out_summary_dict = c_out_summary or _summarize_dict(update)
            else:
                out_summary_dict = _summarize_dict(update)
            event = WorkflowNodeEvent(
                run_id=run_id,
                plugin_id=self.plugin_id,
                node_name=step.name,
                status=node_status,
                summary=summary_text,
                artifact_refs=artifact_refs,
            )
            events_list = list(active_state.get("workflow_events") or [])
            events_list.append(event.model_dump())
            update["workflow_events"] = events_list
            self._events_by_thread.setdefault(thread_id, []).append(event)
            self._node_observations[(thread_id, step.name)] = NodeObservation(
                run_id=run_id,
                plugin_id=self.plugin_id,
                node_name=step.name,
                status=node_status,
                input_summary=_summarize_dict(active_state),
                output_summary=out_summary_dict,
                artifact_refs=artifact_refs,
            )
            if self.hooks.after_node is not None:
                self.hooks.after_node(event, self.context)
            self._audit_log.append(
                HookAuditEntry(
                    thread_id=thread_id,
                    node_name=step.name,
                    action="after_node",
                    detail=summary_text,
                )
            )

            # 第八步：若为尾节点，确保生成标准 output_schema 响应并触发 after_run Hook
            if is_last:
                merged_final = {**active_state, **update}
                if (
                    "structured_response" not in update or "messages" not in update
                ) and merged_final.get("validation_error") is not None:
                    err_out = self.format_error_output(
                        str(merged_final["validation_error"]),
                        status=str(merged_final.get("error_status") or "invalid_input"),
                    )
                    update.update(err_out)
                if self.hooks.after_run is not None:
                    self.hooks.after_run({**active_state, **update}, self.context)
                self._audit_log.append(
                    HookAuditEntry(
                        thread_id=thread_id,
                        node_name=step.name,
                        action="after_run",
                        detail="Workflow run finished",
                    )
                )
            return update

        _node_fn.__name__ = step.name
        return _node_fn

    def build_graph(self) -> CompiledStateGraph[Any, Any, Any, Any]:
        """根据 ``run()`` 声明的 ``WorkflowPlan`` 构建并编译包含独立节点的 LangGraph 状态图。

        :return: 编译完成的 CompiledStateGraph 实例。
        :rtype: langgraph.graph.state.CompiledStateGraph[typing.Any, typing.Any, typing.Any, typing.Any]
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：获取声明式节点计划并初始化三层状态架构 StateGraph
        plan = self.plan
        builder: StateGraph[Any, Any, Any, Any] = StateGraph(
            self.internal_state,
            input_schema=self.input_schema,
            output_schema=self.output_schema,
        )

        # 第二步：将每个 WorkflowStep 注册为独立的 LangGraph 节点
        total = len(plan.steps)
        for idx, step in enumerate(plan.steps):
            node_runner: Any = self._make_node_runner(step, idx, total)
            builder.add_node(step.name, node_runner)

        # 第三步：按照声明顺序连接确定性有向边 START -> step_0 -> ... -> step_n -> END
        builder.add_edge(START, plan.steps[0].name)
        for prev_step, next_step in zip(plan.steps[:-1], plan.steps[1:], strict=False):
            builder.add_edge(prev_step.name, next_step.name)
        builder.add_edge(plan.steps[-1].name, END)

        # 第四步：编译状态图并返回
        return builder.compile(checkpointer=self.checkpointer, name=self.workflow_name)

    def compile(self) -> CompiledStateGraph[Any, Any, Any, Any]:
        """延迟编译并缓存当前工作流的 LangGraph 状态图。

        :return: 编译后的 CompiledStateGraph 实例。
        :rtype: langgraph.graph.state.CompiledStateGraph[typing.Any, typing.Any, typing.Any, typing.Any]
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：若尚未编译则执行 build_graph 并缓存
        if self._compiled_graph is None:
            self._compiled_graph = self.build_graph()
        return self._compiled_graph

    def invoke(
        self,
        input_data: dict[str, Any] | Command[Any] | None,
        config: RunnableConfig | None = None,
        *,
        durability: Durability = "sync",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """同步执行编译后的工作流图，自动维护 thread_id 与 durability。

        :param input_data: 符合 input_schema 的输入字典或恢复指令 Command。
        :type input_data: dict[str, typing.Any] | langgraph.types.Command[typing.Any] | None
        :param config: 可选的 RunnableConfig。
        :type config: langchain_core.runnables.RunnableConfig | None
        :param durability: 检查点同步策略，默认 'sync'。
        :type durability: langgraph.types.Durability
        :param kwargs: 透传给 CompiledStateGraph.invoke 的其他参数。
        :type kwargs: typing.Any
        :return: 符合 output_schema 的执行输出字典。
        :rtype: dict[str, typing.Any]
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：确保编译状态图并补齐默认 thread_id
        graph = self.compile()
        active_config = cast(RunnableConfig, dict(config)) if config is not None else RunnableConfig()
        configurable = dict(active_config.get("configurable") or {})
        if "thread_id" not in configurable:
            configurable["thread_id"] = self.context.thread_id or uuid4().hex
        active_config["configurable"] = configurable

        # 第二步：调用底层状态图同步执行
        if graph.checkpointer is not None and graph.checkpointer is not False:
            return graph.invoke(input_data, config=active_config, durability=durability, **kwargs)
        return graph.invoke(input_data, config=active_config, **kwargs)

    async def ainvoke(
        self,
        input_data: dict[str, Any] | Command[Any] | None,
        config: RunnableConfig | None = None,
        *,
        durability: Durability = "sync",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """异步执行编译后的工作流图。

        :param input_data: 符合 input_schema 的输入字典或恢复指令 Command。
        :type input_data: dict[str, typing.Any] | langgraph.types.Command[typing.Any] | None
        :param config: 可选的 RunnableConfig。
        :type config: langchain_core.runnables.RunnableConfig | None
        :param durability: 检查点同步策略，默认 'sync'。
        :type durability: langgraph.types.Durability
        :param kwargs: 透传给 CompiledStateGraph.ainvoke 的其他参数。
        :type kwargs: typing.Any
        :return: 符合 output_schema 的执行输出字典。
        :rtype: dict[str, typing.Any]
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：确保编译状态图并补齐默认 thread_id
        graph = self.compile()
        active_config = cast(RunnableConfig, dict(config)) if config is not None else RunnableConfig()
        configurable = dict(active_config.get("configurable") or {})
        if "thread_id" not in configurable:
            configurable["thread_id"] = self.context.thread_id or uuid4().hex
        active_config["configurable"] = configurable

        # 第二步：调用底层状态图异步执行
        if graph.checkpointer is not None and graph.checkpointer is not False:
            return await graph.ainvoke(
                input_data, config=active_config, durability=durability, **kwargs
            )
        return await graph.ainvoke(input_data, config=active_config, **kwargs)

    def create_runtime(
        self,
        *,
        checkpointer: BaseCheckpointSaver[Any] | None = None,
    ) -> PluginRuntime:
        """创建绑定持久化检查点的插件运行时实例，用于状态读取、按需观察与节点 Hook 干预。

        :param checkpointer: 可选指定的检查点保存器；未提供时使用工作流自身或内存保存器。
        :type checkpointer: langgraph.checkpoint.base.BaseCheckpointSaver[typing.Any] | None
        :return: 绑定当前工作流的 PluginRuntime 实例。
        :rtype: PluginRuntime
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：若当前工作流未绑定真实 Checkpointer，注入 InMemorySaver 以保障状态快照能力
        if checkpointer is not None:
            self.checkpointer = checkpointer
            self._compiled_graph = None
        elif self.checkpointer is None or self.checkpointer is False:
            self.checkpointer = InMemorySaver()
            self._compiled_graph = None
        return PluginRuntime(self)

    def to_contribution(
        self,
        *,
        mode: WorkflowMode | None = None,
        context_mode: SubAgentContextMode = "isolated",
        durability: Durability = "sync",
        allowed_tenants: frozenset[str] | None = None,
    ) -> WorkflowContribution:
        """将当前工作流导出为符合宿主契约的 WorkflowContribution 对象。

        :param mode: 可选指定的运行模式（compiled 或 async）。
        :type mode: vertical_plugins.common.contracts.WorkflowMode | None
        :param context_mode: 子智能体上下文隔离模式。
        :type context_mode: vertical_plugins.common.contracts.SubAgentContextMode
        :param durability: 检查点同步持久化级别。
        :type durability: langgraph.types.Durability
        :param allowed_tenants: 允许调用的租户白名单。
        :type allowed_tenants: frozenset[str] | None
        :return: 经校验的 WorkflowContribution 实例。
        :rtype: vertical_plugins.common.contracts.WorkflowContribution
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：编译工作流图并构造标准三层状态契约声明
        return WorkflowContribution(
            name=self.workflow_name,
            description=self.description,
            internal_state=self.internal_state,
            input_schema=self.input_schema,
            output_schema=self.output_schema,
            runnable=self.compile(),
            graph_id=self.workflow_name,
            mode=mode,
            context_mode=context_mode,
            durability=durability,
            effect=self.effect,
            max_dispatches=self.max_dispatches,
            idempotent=self.idempotent,
            allowed_tenants=allowed_tenants,
            result_schema=self.result_schema,
        )

    to_workflow_contribution = to_contribution


class PluginRuntime:
    """插件运行实例及按需状态观察、受控节点 Hook 干预统一入口。

    严格执行以下安全边界：
    1. 禁止主智能体增删或重排工作流节点；
    2. 禁止直接修改 `InternalState` 受保护字段或伪装 `as_node` 跳过实际计算；
    3. 修改已完成节点的输入必须通过历史检查点分支重算下游节点；
    4. 按需返回进度、节点摘要与产物引用，不向对话直接倾倒整表或全量内部状态。
    """

    def __init__(self, workflow: BaseWorkflow) -> None:
        """初始化插件运行管理实例。

        :param workflow: 绑定的 BaseWorkflow 实例。
        :type workflow: BaseWorkflow
        :return: 无返回值。
        :rtype: None
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：确保工作流配有可持久化的 Checkpointer
        if workflow.checkpointer is None or workflow.checkpointer is False:
            workflow.checkpointer = InMemorySaver()
            workflow._compiled_graph = None
        self.workflow = workflow
        self.graph = workflow.compile()

    def _get_snapshot(
        self, thread_id: str, checkpoint_id: str | None = None
    ) -> StateSnapshot:
        """读取指定线程与可选检查点 ID 的状态快照。

        :param thread_id: 会话线程标识符。
        :type thread_id: str
        :param checkpoint_id: 可选的历史检查点 ID。
        :type checkpoint_id: str | None
        :return: 对应的 StateSnapshot。
        :rtype: langgraph.types.StateSnapshot
        :raises ValueError: 当指定检查点不存在时抛出。
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：组装查询配置并读取状态
        configurable: dict[str, Any] = {"thread_id": thread_id, "checkpoint_ns": ""}
        if checkpoint_id is not None:
            configurable["checkpoint_id"] = checkpoint_id
        snap = self.graph.get_state({"configurable": configurable})
        if not snap.values and not snap.next and snap.created_at is None:
            raise ValueError(
                f"Checkpoint not found for thread_id={thread_id!r}, checkpoint_id={checkpoint_id!r}"
            )
        return snap

    def invoke(
        self,
        input_data: dict[str, Any] | Command[Any] | None,
        *,
        thread_id: str | None = None,
        run_id: str | None = None,
        pause_before_nodes: Sequence[str] | None = None,
        pause_before_publish: bool | None = None,
    ) -> dict[str, Any]:
        """在指定线程上执行或恢复工作流运行。

        :param input_data: 输入参数字典或恢复 Command。
        :type input_data: dict[str, typing.Any] | langgraph.types.Command[typing.Any] | None
        :param thread_id: 会话线程 ID，省略时自动生成。
        :type thread_id: str | None
        :param run_id: 可选运行 ID。
        :type run_id: str | None
        :param pause_before_nodes: 本次运行需暂停审阅的节点名序列。
        :type pause_before_nodes: collections.abc.Sequence[str] | None
        :param pause_before_publish: 本次运行是否在发布报告前暂停审阅。
        :type pause_before_publish: bool | None
        :return: 工作流执行结果字典。
        :rtype: dict[str, typing.Any]
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：校验用户指定暂停的节点名称必须属于固定工作流计划
        if pause_before_nodes:
            for node in pause_before_nodes:
                self.workflow.plan.get_step(node)

        # 第二步：构建运行配置并委托工作流执行
        resolved_thread = thread_id or self.workflow.context.thread_id or uuid4().hex
        configurable: dict[str, Any] = {"thread_id": resolved_thread}
        if run_id is not None:
            configurable["run_id"] = run_id
        if pause_before_nodes is not None:
            configurable["pause_before_nodes"] = list(pause_before_nodes)
        if pause_before_publish is not None:
            configurable["pause_before_publish"] = pause_before_publish

        return self.workflow.invoke(input_data, config={"configurable": configurable})

    def resume(
        self,
        thread_id: str,
        resume_payload: Any,
        *,
        checkpoint_id: str | None = None,
    ) -> dict[str, Any]:
        """使用 LangGraph 原生 Command(resume=...) 恢复处于 interrupt 暂停状态的工作流。

        :param thread_id: 暂停的线程标识符。
        :type thread_id: str
        :param resume_payload: 用户审阅确认或修改后的恢复载荷。
        :type resume_payload: typing.Any
        :param checkpoint_id: 可选检查点 ID。
        :type checkpoint_id: str | None
        :return: 恢复执行后的工作流输出。
        :rtype: dict[str, typing.Any]
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：构造恢复配置并传入 Command(resume=...)
        configurable: dict[str, Any] = {"thread_id": thread_id}
        if checkpoint_id is not None:
            configurable["checkpoint_id"] = checkpoint_id
        return self.workflow.invoke(
            Command(resume=resume_payload),
            config={"configurable": configurable},
        )

    def get_progress(self, thread_id: str) -> WorkflowProgressSummary:
        """按需查询当前工作流运行进度、所处节点、完成比例与错误摘要。

        :param thread_id: 会话线程标识符。
        :type thread_id: str
        :return: 轻量级进度摘要模型。
        :rtype: WorkflowProgressSummary
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：读取线程状态快照与已记录节点事件
        snap = self._get_snapshot(thread_id)
        events = self.workflow._events_by_thread.get(thread_id, [])
        total = len(self.workflow.plan.steps)
        completed = [e.node_name for e in events if e.status == "completed"]
        failed = [e for e in events if e.status == "failed"]
        run_id = events[-1].run_id if events else (self.workflow.context.run_id or "")

        # 第二步：根据快照中断状态、待执行节点与错误事件判定整体状态
        if snap.interrupts:
            status: Literal["pending", "running", "interrupted", "completed", "failed"] = (
                "interrupted"
            )
        elif failed or snap.values.get("validation_error"):
            status = "failed"
        elif snap.next:
            status = "running" if completed else "pending"
        else:
            status = "completed"

        current_node = snap.next[0] if snap.next else (events[-1].node_name if events else None)
        error_summary = (
            str(snap.values.get("validation_error"))
            if snap.values.get("validation_error")
            else (failed[-1].summary if failed else None)
        )
        return WorkflowProgressSummary(
            run_id=run_id,
            plugin_id=self.workflow.plugin_id,
            thread_id=thread_id,
            current_node=current_node,
            completed_nodes=completed,
            total_nodes=total,
            progress_ratio=round(len(completed) / total, 4) if total else 1.0,
            status=status,
            error_summary=error_summary,
        )

    def get_node_summary(self, thread_id: str, node_name: str) -> NodeObservation:
        """按需查询指定节点的输入摘要、输出摘要与产物引用（不返回完整大表）。

        :param thread_id: 会话线程标识符。
        :type thread_id: str
        :param node_name: 目标节点名称。
        :type node_name: str
        :return: 节点输入输出脱敏观察摘要。
        :rtype: NodeObservation
        :raises ValueError: 当节点名称非法或该节点尚未产生观察记录时抛出。
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：校验节点名合法并检索缓存的观察摘要
        self.workflow.plan.get_step(node_name)
        obs = self.workflow._node_observations.get((thread_id, node_name))
        if obs is not None:
            return obs

        # 第二步：若节点尚未运行，从当前快照提取待执行输入摘要
        snap = self._get_snapshot(thread_id)
        return NodeObservation(
            run_id=self.workflow.context.run_id or "",
            plugin_id=self.workflow.plugin_id,
            node_name=node_name,
            status="pending",
            input_summary=_summarize_dict(snap.values if isinstance(snap.values, dict) else {}),
            output_summary={},
            artifact_refs=[],
        )

    def inspect_debug_state(
        self, thread_id: str, *, authorized: bool = True
    ) -> dict[str, Any]:
        """经权限校验后读取必要的详细状态数据，用于深度调试审查。

        :param thread_id: 会话线程标识符。
        :type thread_id: str
        :param authorized: 是否已通过宿主调试审查权限校验。
        :type authorized: bool
        :return: 当前检查点状态的深拷贝字典（包含 values 字段）。
        :rtype: dict[str, typing.Any]
        :raises PermissionError: 当未获得调试审查授权时抛出。
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：强制校验调试审查授权标志
        if not authorized:
            raise PermissionError("Inspecting detailed internal workflow state requires authorization")
        # 第二步：读取并返回包含 values 的快照状态字典
        snap = self._get_snapshot(thread_id)
        values = deepcopy(dict(snap.values)) if isinstance(snap.values, dict) else {}
        return {
            "values": values,
            "next": snap.next,
            "config": snap.config,
            "metadata": snap.metadata,
        }

    def modify_node_input(
        self,
        thread_id: str,
        node_name: str,
        values: dict[str, Any],
        *,
        checkpoint_id: str | None = None,
        fork_thread_id: str | None = None,
    ) -> RunnableConfig:
        """修改指定节点的可编辑输入字段；若该节点已完成，则强制从前置检查点分支重算下游节点。

        严格禁止：
        - 伪造不存在的节点名称；
        - 修改未在节点 `editable_fields` 白名单中声明的字段或 `InternalState` 受保护字段；
        - 伪装 `as_node=node_name` 跳过该节点的实际计算或直接覆盖已完成节点的旧结果。

        :param thread_id: 源会话线程标识符。
        :type thread_id: str
        :param node_name: 目标工作流节点名称。
        :type node_name: str
        :param values: 待修改的可编辑输入字段字典。
        :type values: dict[str, typing.Any]
        :param checkpoint_id: 可选的历史检查点 ID。
        :type checkpoint_id: str | None
        :param fork_thread_id: 可选的新分支线程 ID（跨线程分叉时指定）。
        :type fork_thread_id: str | None
        :return: 指向更新或分叉后新检查点的 RunnableConfig（可继续 invoke 重算下游节点）。
        :rtype: langchain_core.runnables.RunnableConfig
        :raises ValueError: 当节点名非法、字段越权或找不到前置重算检查点时抛出。
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：执行严格的节点名、白名单与 Pydantic 输入校验
        validated = self.workflow.validate_node_modification(node_name, values)
        node_names = self.workflow.plan.node_names
        node_idx = node_names.index(node_name)
        predecessor_as_node = "__start__" if node_idx == 0 else node_names[node_idx - 1]

        # 第二步：定位目标节点尚未执行前的前置检查点快照（若节点已完成，回溯至该节点执行前的检查点以重算下游）
        target_snap: StateSnapshot | None = None
        if checkpoint_id is not None:
            candidate = self._get_snapshot(thread_id, checkpoint_id)
            if node_name in (candidate.next or ()) or (not candidate.next and node_idx == 0):
                target_snap = candidate
            else:
                raise ValueError(
                    f"Checkpoint {checkpoint_id!r} is not positioned before node {node_name!r}; "
                    "modifying a completed node requires branching from the checkpoint before it ran."
                )
        else:
            current = self._get_snapshot(thread_id)
            if node_name in (current.next or ()):
                target_snap = current
            else:
                for snap in self.graph.get_state_history(
                    {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
                ):
                    if node_name in (snap.next or ()):
                        target_snap = snap
                        break
                if target_snap is None and node_idx == 0:
                    history = list(
                        self.graph.get_state_history(
                            {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
                        )
                    )
                    if history:
                        target_snap = history[-1]

        if target_snap is None:
            raise ValueError(
                f"Cannot locate pre-execution checkpoint for node {node_name!r} on thread {thread_id!r}"
            )

        # 第三步：若同時存在于 validated_input 中，同步更新内部已校验输入副本，使下游节点重算生效
        state_updates: dict[str, Any] = dict(validated)
        existing_validated = (
            target_snap.values.get("validated_input")
            if isinstance(target_snap.values, dict)
            else None
        )
        if isinstance(existing_validated, dict):
            merged_vi = dict(existing_validated)
            merged_vi.update(validated)
            state_updates["validated_input"] = merged_vi

        # 第四步：若指定了新线程 fork_thread_id，克隆检查点到新分支线程后再应用更新
        source_cfg = target_snap.config
        if fork_thread_id is not None and fork_thread_id != thread_id:
            saver = self.graph.checkpointer
            if saver is None or isinstance(saver, bool):
                raise ValueError("Graph checkpointer is required for cross-thread fork")
            tup = saver.get_tuple(source_cfg)
            if tup is None:
                raise ValueError("Source checkpoint tuple not found")
            dest_base: RunnableConfig = {
                "configurable": {"thread_id": fork_thread_id, "checkpoint_ns": ""}
            }
            if saver.get_tuple(dest_base) is not None:
                raise ValueError(f"Fork destination thread already exists: {fork_thread_id}")
            cloned_cfg = saver.put(
                dest_base,
                deepcopy(tup.checkpoint),
                deepcopy(tup.metadata) if tup.metadata else {"source": "fork", "step": 0},
                dict(tup.checkpoint.get("channel_versions", {})),
            )
            pending: dict[str, list[tuple[str, Any]]] = {}
            for task_id, channel, value in tup.pending_writes or ():
                pending.setdefault(task_id, []).append((channel, deepcopy(value)))
            for task_id, writes in pending.items():
                saver.put_writes(cloned_cfg, writes, task_id)
            updated_cfg = self.graph.update_state(
                cloned_cfg, state_updates, as_node=predecessor_as_node
            )
            audit_thread = fork_thread_id
        else:
            updated_cfg = self.graph.update_state(
                source_cfg, state_updates, as_node=predecessor_as_node
            )
            audit_thread = thread_id

        # 第五步：写入不可变审计日志并返回指向分支检查点的配置
        new_cid = updated_cfg.get("configurable", {}).get("checkpoint_id")
        self.workflow._audit_log.append(
            HookAuditEntry(
                thread_id=audit_thread,
                node_name=node_name,
                action="modify_input",
                fields_modified=tuple(validated.keys()),
                checkpoint_id=str(new_cid) if new_cid else None,
                detail=f"Branch prepared before node {node_name} (as_node={predecessor_as_node})",
            )
        )
        return updated_cfg

    def fork_branch(
        self,
        thread_id: str,
        node_name: str,
        *,
        new_thread_id: str,
        updates: dict[str, Any] | None = None,
        checkpoint_id: str | None = None,
    ) -> RunnableConfig:
        """从指定节点的前置检查点派生新分支线程，并应用白名单输入修改。

        :param thread_id: 原会话线程标识符。
        :type thread_id: str
        :param node_name: 目标工作流节点名称。
        :type node_name: str
        :param new_thread_id: 派生的新分支线程标识符。
        :type new_thread_id: str
        :param updates: 节点输入更新字典。
        :type updates: dict[str, typing.Any] | None
        :param checkpoint_id: 可选的历史检查点 ID。
        :type checkpoint_id: str | None
        :return: 指向新分支检查点的 RunnableConfig。
        :rtype: langchain_core.runnables.RunnableConfig
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：委托给 modify_node_input 完成跨线程克隆与节点前置状态修改
        return self.modify_node_input(
            thread_id,
            node_name,
            values=updates or {},
            checkpoint_id=checkpoint_id,
            fork_thread_id=new_thread_id,
        )

    def retry_node(
        self,
        thread_id: str,
        node_name: str,
        *,
        values: dict[str, Any] | None = None,
        checkpoint_id: str | None = None,
        fork_thread_id: str | None = None,
    ) -> dict[str, Any]:
        """从指定节点的前置检查点重试该节点及其受影响的下游节点。

        :param thread_id: 源会话线程标识符。
        :type thread_id: str
        :param node_name: 要重试的节点名称。
        :type node_name: str
        :param values: 可选在重试前修改的该节点可编辑输入字典。
        :type values: dict[str, typing.Any] | None
        :param checkpoint_id: 可选的历史检查点 ID。
        :type checkpoint_id: str | None
        :param fork_thread_id: 可选派生的新分支线程 ID。
        :type fork_thread_id: str | None
        :return: 重算完成后的工作流最终状态输出字典。
        :rtype: dict[str, typing.Any]
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：校验节点存在性并定位或更新前置检查点
        self.workflow.plan.get_step(node_name)
        if values is not None:
            branch_cfg = self.modify_node_input(
                thread_id,
                node_name,
                values,
                checkpoint_id=checkpoint_id,
                fork_thread_id=fork_thread_id,
            )
        else:
            node_names = self.workflow.plan.node_names
            node_idx = node_names.index(node_name)
            predecessor_as_node = "__start__" if node_idx == 0 else node_names[node_idx - 1]
            target_snap: StateSnapshot | None = None
            if checkpoint_id is not None:
                target_snap = self._get_snapshot(thread_id, checkpoint_id)
            else:
                for snap in self.graph.get_state_history(
                    {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
                ):
                    if node_name in (snap.next or ()):
                        target_snap = snap
                        break
            if target_snap is None:
                raise ValueError(f"Cannot find checkpoint before node {node_name!r} to retry")
            if fork_thread_id is not None and fork_thread_id != thread_id:
                saver = self.graph.checkpointer
                assert saver is not None and not isinstance(saver, bool)
                tup = saver.get_tuple(target_snap.config)
                assert tup is not None
                dest_base: RunnableConfig = {
                    "configurable": {"thread_id": fork_thread_id, "checkpoint_ns": ""}
                }
                cloned_cfg = saver.put(
                    dest_base,
                    deepcopy(tup.checkpoint),
                    deepcopy(tup.metadata) if tup.metadata else {"source": "fork", "step": 0},
                    dict(tup.checkpoint.get("channel_versions", {})),
                )
                for task_id, channel, value in tup.pending_writes or ():
                    saver.put_writes(cloned_cfg, [(channel, deepcopy(value))], task_id)
                branch_cfg = cloned_cfg
            else:
                branch_cfg = self.graph.update_state(
                    target_snap.config, {}, as_node=predecessor_as_node
                )

        # 第二步：记录重试审计日志并从分叉检查点继续运行下游流程
        active_thread = fork_thread_id or thread_id
        cid = branch_cfg.get("configurable", {}).get("checkpoint_id")
        self.workflow._audit_log.append(
            HookAuditEntry(
                thread_id=active_thread,
                node_name=node_name,
                action="retry",
                fields_modified=tuple(values.keys()) if values else (),
                checkpoint_id=str(cid) if cid else None,
                detail=f"Retrying from node {node_name}",
            )
        )
        return self.workflow.invoke(None, config=branch_cfg)

    def get_events(self, thread_id: str) -> list[WorkflowNodeEvent]:
        """获取指定线程已记录的结构化节点事件列表。

        :param thread_id: 会话线程标识符。
        :type thread_id: str
        :return: WorkflowNodeEvent 列表。
        :rtype: list[WorkflowNodeEvent]
        :author: tongliga
        :date: 2026-10-19
        """
        return list(self.workflow._events_by_thread.get(thread_id, ()))

    def get_audit_log(self, thread_id: str | None = None) -> list[HookAuditEntry]:
        """查询 Hook 与节点干预的审计记录列表。

        :param thread_id: 可选按线程 ID 过滤。
        :type thread_id: str | None
        :return: HookAuditEntry 列表。
        :rtype: list[HookAuditEntry]
        :author: tongliga
        :date: 2026-10-19
        """
        if thread_id is None:
            return list(self.workflow._audit_log)
        return [entry for entry in self.workflow._audit_log if entry.thread_id == thread_id]


def extract_message_text(message: AnyMessage | dict[str, Any]) -> str:
    """从 LangChain 消息对象或字典中提取纯文本。

    :param message: 消息实例或字典。
    :type message: langchain_core.messages.AnyMessage | dict[str, typing.Any]
    :return: 提取出的纯文本字符串。
    :rtype: str
    :author: tongliga
    :date: 2026-10-19
    """
    # 第一步：获取 content 字段并展平文本块
    content = (
        message.get("content", "") if isinstance(message, dict) else getattr(message, "content", "")
    )
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            block.get("text", "") if isinstance(block, dict) else str(block) for block in content
        )
    return str(content)


def extract_json_payload(text: str) -> dict[str, Any] | None:
    """从消息文本中提取 JSON 字典对象（兼容 ```json 代码块与内嵌 JSON 对象）。

    :param text: 待解析的原始文本。
    :type text: str
    :return: 解析成功的字典，或 None。
    :rtype: dict[str, typing.Any] | None
    :author: tongliga
    :date: 2026-10-19
    """
    # 第一步：裁剪空白并收集候选 JSON 片段
    stripped = text.strip()
    if not stripped:
        return None
    candidates: list[str] = [stripped]
    if "```" in stripped:
        for block in stripped.split("```"):
            cleaned = block.removeprefix("json").strip()
            if cleaned.startswith("{") and cleaned.endswith("}"):
                candidates.insert(0, cleaned)
    first_brace, last_brace = stripped.find("{"), stripped.rfind("}")
    if 0 <= first_brace < last_brace:
        candidates.append(stripped[first_brace : last_brace + 1])

    # 第二步：尝试反序列化为 dict
    for candidate in candidates:
        try:
            loaded = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(loaded, dict):
            return loaded
    return None


__all__ = [
    "DEFAULT_PROTECTED_STATE_FIELDS",
    "AuthorizationGuardProtocol",
    "BaseWorkflow",
    "HookAuditEntry",
    "NodeObservation",
    "PluginRuntime",
    "WorkflowContext",
    "WorkflowHooks",
    "WorkflowNodeEvent",
    "WorkflowPlan",
    "WorkflowProgressSummary",
    "WorkflowStep",
    "extract_json_payload",
    "extract_message_text",
]
