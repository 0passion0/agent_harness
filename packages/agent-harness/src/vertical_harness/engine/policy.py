"""Supervisor 确定性监督策略中间件与统一插件执行授权网关（PluginAuthorizationGateway）。

核心职责：
1. 严格区分插件已安装（installed）、已启用（enabled）与已授权（authorized）三种状态；
2. 维护 `discovered -> pending_approval -> authorized -> running -> completed/failed/cancelled` 状态机；
3. 拒绝任何由模型生成的 `approved=True`、普通消息文本或可伪造工具参数作为授权依据；
4. 统一校验 `task`、`start_async_task`、插件工具、Hook 与直接运行入口的授权边界、数据范围及副作用级别；
5. 执行工作流白名单、租户 RBAC、前置依赖顺序、最大委派次数、幂等缓存与输出 Schema 验收。
"""

from __future__ import annotations

import hashlib
import logging
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import replace
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from langchain.agents.middleware import (
    AgentMiddleware,
    InterruptOnConfig,
    ModelCallLimitMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.types import Command, interrupt
from pydantic import BaseModel, Field, ValidationError

from vertical_plugins.common.contracts import (
    RunContext,
    SideEffect,
    StrictModel,
    WorkflowContribution,
    WorkflowMode,
)
from vertical_plugins.common.workflow import extract_json_payload, extract_message_text

logger = logging.getLogger(__name__)

# Deep Agents 同步与异步子任务委派工具名称集合
_DISPATCH_TOOLS = frozenset({"task", "start_async_task"})
# Deep Agents 异步子任务状态监控与管理生命周期工具名称集合
_MONITOR_TOOLS = frozenset(
    {"check_async_task", "update_async_task", "cancel_async_task", "list_async_tasks"}
)
# 严禁模型在工具参数或任务 JSON 中伪造的授权上下文字段
_FORBIDDEN_MODEL_AUTH_KEYS = frozenset(
    {"approved", "authorized", "is_authorized", "user_approved", "bypass_approval", "auth_token"}
)


class FinalModelCallLimitMiddleware(ModelCallLimitMiddleware):
    """Use the final allowed run call to summarize verified progress without tools."""

    _FINAL_CALL_INSTRUCTION = (
        "The model-call budget for this run is exhausted after this response. Do not use tools "
        "or claim further execution. Based only on verified results already available, briefly "
        "state what is complete, what remains incomplete, and the paths of any verified files. "
        "Reply directly in the user's language using ordinary prose, never tool-call markup "
        "or instructions to invoke a tool. Do not invent findings or deliverables."
    )

    def __init__(self, *, run_limit: int) -> None:
        super().__init__(run_limit=run_limit)
        self._final_call_count = run_limit - 1

    def _final_call_request(self, request: ModelRequest[Any]) -> ModelRequest[Any]:
        if request.state.get("run_model_call_count", 0) != self._final_call_count:
            return request
        existing = request.system_prompt or ""
        return request.override(
            system_message=SystemMessage(
                content=f"{existing}\n\n{self._FINAL_CALL_INSTRUCTION}".strip()
            ),
            tools=[],
            tool_choice="none",
        )

    def wrap_model_call(self, request: ModelRequest[Any], handler: Any) -> ModelResponse:
        return handler(self._final_call_request(request))

    async def awrap_model_call(self, request: ModelRequest[Any], handler: Any) -> ModelResponse:
        return await handler(self._final_call_request(request))


class SupervisorStage(StrEnum):
    """主智能体（Supervisor）监督工作流生命周期阶段枚举。"""

    PLAN = "plan"
    DISPATCH = "dispatch"
    MONITOR = "monitor"
    VALIDATE = "validate"
    FINALIZE = "finalize"


class PluginAuthState(StrEnum):
    """插件执行授权网关状态机枚举。"""

    DISCOVERED = "discovered"
    PENDING_APPROVAL = "pending_approval"
    AUTHORIZED = "authorized"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


def _normalize_scope_item(raw: str) -> str:
    """规范化输入文件或数据范围标识以便精确比对。

    :param raw: 原始路径或范围标识字符串。
    :type raw: str
    :return: 规范化后的范围字符串。
    :rtype: str
    :author: tongliga
    :date: 2026-10-19
    """
    # 第一步：剥离虚拟 /workspace/ 前缀与多余斜杠
    cleaned = raw.strip().replace("\\", "/")
    if cleaned.startswith("/workspace/"):
        cleaned = cleaned.removeprefix("/workspace/").lstrip("/")
    return cleaned.lstrip("./")


def _extract_payload_from_input(input_data: dict[str, Any] | None) -> dict[str, Any]:
    """从工作流输入状态或消息列表中提取业务参数字典。

    :param input_data: 原始输入字典。
    :type input_data: dict[str, typing.Any] | None
    :return: 合并后的业务参数字典。
    :rtype: dict[str, typing.Any]
    :author: tongliga
    :date: 2026-10-19
    """
    # 第一步：处理空输入
    if not input_data or not isinstance(input_data, dict):
        return {}
    merged = dict(input_data)
    # 第二步：若包含 messages 或 description，尝试提取内嵌 JSON 载荷
    if "description" in merged and isinstance(merged["description"], str):
        parsed_desc = extract_json_payload(merged["description"])
        if parsed_desc:
            merged = {**parsed_desc, **merged}
    messages = merged.get("messages")
    if isinstance(messages, list) and messages:
        parsed_msg = extract_json_payload(extract_message_text(messages[-1]))
        if parsed_msg:
            merged = {**parsed_msg, **merged}
    return merged


def _contains_spoofed_auth_flag(payload: dict[str, Any]) -> str | None:
    """检查参数字典中是否包含模型伪造的授权标记字段。

    :param payload: 待检测的参数字典。
    :type payload: dict[str, typing.Any]
    :return: 命中的伪造字段名，未命中返回 None。
    :rtype: str | None
    :author: tongliga
    :date: 2026-10-19
    """
    # 第一步：检查顶层键是否包含受禁授权字段
    for key in _FORBIDDEN_MODEL_AUTH_KEYS:
        if key in payload:
            return key
    return None


class PluginExecutionRequest(StrictModel):
    """主 Agent 提出的插件执行请求（展示给用户进行确认的完整契约）。"""

    request_id: str
    plugin_id: str
    workflow_name: str
    purpose: str
    input_scope: tuple[str, ...] = ()
    planned_outputs: tuple[str, ...] = ()
    allowed_effects: tuple[SideEffect, ...] = (SideEffect.READ,)
    user_id: str = "default"
    tenant_id: str = "default"
    thread_id: str = "default"
    run_id: str | None = None
    state: PluginAuthState = PluginAuthState.PENDING_APPROVAL
    rejection_reason: str | None = None

    def format_user_prompt(self) -> str:
        """生成面向用户确认的清晰授权提示（展示插件名称、操作目的、输入数据范围及计划输出文件）。

        :return: 格式化后的中文确认提示文本。
        :rtype: str
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：格式化输入范围、输出文件与副作用列表
        scope_str = ", ".join(self.input_scope) if self.input_scope else "当前会话内联输入"
        outputs_str = (
            ", ".join(self.planned_outputs)
            if self.planned_outputs
            else "无落盘文件（仅结构化响应）"
        )
        effects_str = ", ".join(eff.value for eff in self.allowed_effects)
        return (
            f"【插件执行授权请求】\n"
            f"- 插件名称：{self.plugin_id} ({self.workflow_name})\n"
            f"- 操作目的：{self.purpose}\n"
            f"- 输入数据范围：{scope_str}\n"
            f"- 计划输出文件：{outputs_str}\n"
            f"- 允许副作用：{effects_str}"
        )

    def format_confirmation_prompt(self) -> str:
        """生成面向用户确认的清晰授权提示（format_user_prompt 的方法别名）。

        :return: 格式化后的中文确认提示文本。
        :rtype: str
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：委托给 format_user_prompt 统一生成确认提示
        return self.format_user_prompt()


class PluginApprovalRecord(StrictModel):
    """由可信宿主维护的插件执行批准记录（不可由模型参数伪造）。"""

    approval_id: str
    request_id: str
    user_id: str
    tenant_id: str
    thread_id: str
    plugin_id: str
    workflow_name: str
    input_scope: tuple[str, ...] = ()
    planned_outputs: tuple[str, ...] = ()
    allowed_effects: frozenset[SideEffect] = Field(
        default_factory=lambda: frozenset({SideEffect.READ})
    )
    run_id: str | None = None
    active: bool = True


class PluginAuthorizationGateway:
    """统一插件执行授权网关（实现 `AuthorizationGuardProtocol`）。

    严格区分：
    1. 插件已安装（installed）：本地 Entry Point 或显式列表可发现；
    2. 插件已启用（enabled）：宿主配置 `enabled_plugins` 允许该插件存在；
    3. 插件已授权（authorized）：当前用户通过宿主接口明确批准某次具体执行。
    """

    def __init__(
        self,
        *,
        installed_plugins: Iterable[str] = (),
        enabled_plugins: Iterable[str] = (),
        remember_authorizations: bool = False,
    ) -> None:
        """初始化插件授权网关。

        :param installed_plugins: 已安装的插件 ID 集合。
        :type installed_plugins: collections.abc.Iterable[str]
        :param enabled_plugins: 宿主已启用的插件 ID 集合。
        :type enabled_plugins: collections.abc.Iterable[str]
        :param remember_authorizations: 批准后授予该身份持续使用插件的权限；持久化由可信宿主负责。
        :type remember_authorizations: bool
        :return: 无返回值。
        :rtype: None
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：初始化已安装、已启用插件集合与状态表
        self._installed: set[str] = set(installed_plugins)
        self._enabled: set[str] = set(enabled_plugins)
        self._installed.update(self._enabled)
        self._workflow_to_plugin: dict[str, str] = {}
        self._requests: dict[str, PluginExecutionRequest] = {}
        self._approvals: dict[str, PluginApprovalRecord] = {}
        self._state_by_key: dict[tuple[str, str], PluginAuthState] = {}
        self._rejected_keys: set[tuple[str, str]] = set()
        self._selected_by_thread: dict[str, tuple[str, frozenset[str]]] = {}
        self._execution_requests: dict[str, str] = {}
        self._remember_authorizations = remember_authorizations
        self._authorized_plugins: set[tuple[str, str, str]] = set()

    def authorize_plugin(self, plugin_id: str, *, user_id: str, tenant_id: str) -> None:
        """Restore a plugin grant from trusted host storage, never from model input."""
        self._authorized_plugins.add((tenant_id, user_id, plugin_id))

    def select_for_run(self, thread_id: str, run_id: str, plugin_ids: Iterable[str]) -> None:
        selected = frozenset(plugin_ids)
        if not selected <= self._enabled:
            raise ValueError("Selected plugins must be enabled by the host")
        self._selected_by_thread[thread_id] = (run_id, selected)

    def end_run(self, thread_id: str, run_id: str) -> None:
        if self._selected_by_thread.get(thread_id, (None,))[0] == run_id:
            del self._selected_by_thread[thread_id]
        for approval in self._approvals.values():
            if approval.thread_id == thread_id and approval.run_id == run_id:
                approval.active = False

    def selected_plugins(self, thread_id: str, run_id: str) -> frozenset[str]:
        selection = self._selected_by_thread.get(thread_id)
        if selection is None or selection[0] != run_id:
            return frozenset()
        return selection[1]

    def restore_pending_request(
        self,
        value: dict[str, Any],
        *,
        user_id: str,
        tenant_id: str,
        thread_id: str,
        run_id: str,
    ) -> PluginExecutionRequest:
        """Rehydrate only a plugin request explicitly present in a graph interrupt."""
        request = PluginExecutionRequest(
            request_id=value["request_id"],
            plugin_id=value["plugin_id"],
            workflow_name=value["workflow_name"],
            purpose=value["purpose"],
            input_scope=tuple(value["input_scope"]),
            planned_outputs=tuple(value["planned_outputs"]),
            allowed_effects=tuple(SideEffect(effect) for effect in value["allowed_effects"]),
            user_id=user_id,
            tenant_id=tenant_id,
            thread_id=thread_id,
            run_id=run_id,
        )
        self._requests[request.request_id] = request
        self._workflow_to_plugin[request.workflow_name] = request.plugin_id
        self._state_by_key[(thread_id, request.plugin_id)] = PluginAuthState.PENDING_APPROVAL
        self._state_by_key[(thread_id, request.workflow_name)] = PluginAuthState.PENDING_APPROVAL
        return request

    def register_plugin(
        self,
        plugin_id: str,
        *,
        enabled: bool = True,
        workflow_names: Iterable[str] = (),
    ) -> None:
        """向网关登记已安装/已启用的插件及其工作流映射，初始状态为 `discovered`。

        :param plugin_id: 插件唯一标识符。
        :type plugin_id: str
        :param enabled: 是否由宿主启用。
        :type enabled: bool
        :param workflow_names: 该插件包含的工作流名称序列。
        :type workflow_names: collections.abc.Iterable[str]
        :return: 无返回值。
        :rtype: None
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：记录安装与启用状态，默认仅处于 discovered，绝不自动获得 authorized
        self._installed.add(plugin_id)
        if enabled:
            self._enabled.add(plugin_id)
        for wf_name in workflow_names:
            self._workflow_to_plugin[wf_name] = plugin_id
            self._state_by_key[("default", wf_name)] = PluginAuthState.DISCOVERED
        self._state_by_key[("default", plugin_id)] = PluginAuthState.DISCOVERED

    def bind_workflow(self, workflow_name: str, plugin_id: str) -> None:
        """绑定工作流名称与所属插件 ID 的映射关系。

        :param workflow_name: 工作流名称。
        :type workflow_name: str
        :param plugin_id: 所属插件 ID。
        :type plugin_id: str
        :return: 无返回值。
        :rtype: None
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：记录工作流到插件的映射并初始化默认状态为 discovered
        self._workflow_to_plugin[workflow_name] = plugin_id
        self._state_by_key.setdefault(("default", workflow_name), PluginAuthState.DISCOVERED)

    def bind_tool(self, tool_name: str, plugin_id: str) -> None:
        """绑定插件工具名称与所属插件 ID 的映射关系。

        :param tool_name: 工具名称。
        :type tool_name: str
        :param plugin_id: 所属插件 ID。
        :type plugin_id: str
        :return: 无返回值。
        :rtype: None
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：记录工具名称到插件的映射并初始化默认状态为 discovered
        self._workflow_to_plugin[tool_name] = plugin_id
        self._state_by_key.setdefault(("default", tool_name), PluginAuthState.DISCOVERED)

    def is_installed(self, plugin_id: str) -> bool:
        """检查插件是否已安装。

        :param plugin_id: 插件 ID。
        :type plugin_id: str
        :return: 是否已安装。
        :rtype: bool
        :author: tongliga
        :date: 2026-10-19
        """
        return plugin_id in self._installed

    def is_enabled(self, plugin_id: str) -> bool:
        """检查插件是否已由宿主启用。

        :param plugin_id: 插件 ID。
        :type plugin_id: str
        :return: 是否已启用。
        :rtype: bool
        :author: tongliga
        :date: 2026-10-19
        """
        return plugin_id in self._enabled

    def is_authorized(
        self,
        plugin_or_workflow: str,
        *,
        thread_id: str = "default",
        user_id: str = "default",
        tenant_id: str = "default",
    ) -> bool:
        """检查指定插件或工作流在当前会话下是否已获得有效用户授权。

        :param plugin_or_workflow: 插件 ID 或工作流名称。
        :type plugin_or_workflow: str
        :param thread_id: 会话线程 ID。
        :type thread_id: str
        :param user_id: 用户 ID。
        :type user_id: str
        :param tenant_id: 租户 ID。
        :type tenant_id: str
        :return: 若存在当前有效的用户批准记录则返回 True，否则返回 False。
        :rtype: bool
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：查找匹配的活跃批准记录
        plugin_id = self._workflow_to_plugin.get(plugin_or_workflow, plugin_or_workflow)
        return (tenant_id, user_id, plugin_id) in self._authorized_plugins or (
            self._find_active_approval(
                plugin_or_workflow=plugin_or_workflow,
                thread_id=thread_id,
                user_id=user_id,
                tenant_id=tenant_id,
            )
            is not None
        )

    def get_state(self, plugin_or_workflow: str, *, thread_id: str = "default") -> PluginAuthState:
        """查询插件或工作流在指定线程下的当前授权生命周期状态。

        :param plugin_or_workflow: 插件 ID 或工作流名称。
        :type plugin_or_workflow: str
        :param thread_id: 会话线程 ID。
        :type thread_id: str
        :return: 当前状态枚举值。
        :rtype: PluginAuthState
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：优先返回当前线程状态，未触发请求时返回 discovered
        if (thread_id, plugin_or_workflow) in self._state_by_key:
            return self._state_by_key[(thread_id, plugin_or_workflow)]
        return self._state_by_key.get(("default", plugin_or_workflow), PluginAuthState.DISCOVERED)

    def request_execution(
        self,
        *,
        plugin_id: str,
        workflow_name: str,
        purpose: str,
        input_scope: Sequence[str] = (),
        planned_outputs: Sequence[str] = (),
        allowed_effects: Iterable[SideEffect] = (SideEffect.READ,),
        user_id: str = "default",
        tenant_id: str = "default",
        thread_id: str = "default",
        run_id: str | None = None,
    ) -> PluginExecutionRequest:
        """创建插件运行请求，将状态推进至 `pending_approval` 等待用户确认。

        :param plugin_id: 插件 ID。
        :type plugin_id: str
        :param workflow_name: 工作流名称。
        :type workflow_name: str
        :param purpose: 操作目的说明。
        :type purpose: str
        :param input_scope: 输入数据范围（如工作区文件路径列表）。
        :type input_scope: collections.abc.Sequence[str]
        :param planned_outputs: 计划输出文件列表。
        :type planned_outputs: collections.abc.Sequence[str]
        :param allowed_effects: 请求允许的副作用级别集合。
        :type allowed_effects: collections.abc.Iterable[vertical_plugins.common.contracts.SideEffect]
        :param user_id: 请求用户 ID。
        :type user_id: str
        :param tenant_id: 请求租户 ID。
        :type tenant_id: str
        :param thread_id: 当前会话线程 ID。
        :type thread_id: str
        :param run_id: 可选预绑定的运行 ID。
        :type run_id: str | None
        :return: 待用户确认的 PluginExecutionRequest。
        :rtype: PluginExecutionRequest
        :raises PermissionError: 当插件未启用或已被当前用户拒绝时抛出。
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：校验插件是否已启用且未被用户拒绝
        if plugin_id not in self._enabled:
            raise PermissionError(f"Plugin '{plugin_id}' is not enabled by host.")
        if (run_id or thread_id, workflow_name) in self._rejected_keys or (
            run_id or thread_id,
            plugin_id,
        ) in self._rejected_keys:
            raise PermissionError(
                f"Execution of plugin '{plugin_id}' ({workflow_name}) was already rejected by user in thread '{thread_id}'."
            )

        # 第二步：规范化输入数据范围、计划输出文件与允许副作用
        norm_scope = tuple(_normalize_scope_item(s) for s in input_scope if str(s).strip())
        norm_outputs = tuple(str(o).strip() for o in planned_outputs if str(o).strip())
        effects_tuple = tuple(dict.fromkeys(allowed_effects))

        # 第三步：构建请求对象并将状态更新为 pending_approval
        existing = next(
            (
                item
                for item in self._requests.values()
                if item.state == PluginAuthState.PENDING_APPROVAL
                and item.thread_id == thread_id
                and item.run_id == run_id
                and item.plugin_id == plugin_id
                and item.workflow_name == workflow_name
                and item.input_scope == norm_scope
                and item.planned_outputs == norm_outputs
                and item.allowed_effects == effects_tuple
            ),
            None,
        )
        if existing is not None:
            return existing
        req = PluginExecutionRequest(
            request_id=f"req-{uuid4().hex[:12]}",
            plugin_id=plugin_id,
            workflow_name=workflow_name,
            purpose=purpose.strip() or f"Execute {workflow_name}",
            input_scope=norm_scope,
            planned_outputs=norm_outputs,
            allowed_effects=effects_tuple,
            user_id=user_id,
            tenant_id=tenant_id,
            thread_id=thread_id,
            run_id=run_id,
            state=PluginAuthState.PENDING_APPROVAL,
        )
        self._requests[req.request_id] = req
        self._workflow_to_plugin[workflow_name] = plugin_id
        self._state_by_key[(thread_id, plugin_id)] = PluginAuthState.PENDING_APPROVAL
        self._state_by_key[(thread_id, workflow_name)] = PluginAuthState.PENDING_APPROVAL
        return req

    def approve_request(
        self,
        request_id: str,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
        run_id: str | None = None,
    ) -> PluginApprovalRecord:
        """由可信宿主调用：记录用户对指定插件运行请求的明确批准，状态推进至 `authorized`。

        :param request_id: 待批准的请求 ID。
        :type request_id: str
        :param user_id: 核验批准操作的用户 ID（若提供则必须与请求用户匹配）。
        :type user_id: str | None
        :param tenant_id: 核验批准操作的租户 ID（若提供则必须与请求租户匹配）。
        :type tenant_id: str | None
        :param run_id: 可选绑定的单次运行 ID。
        :type run_id: str | None
        :return: 宿主维护的批准记录对象。
        :rtype: PluginApprovalRecord
        :raises ValueError: 当请求不存在或状态不是 pending_approval 时抛出。
        :raises PermissionError: 当批准人身份与请求租户/用户不匹配时抛出。
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：获取请求并校验其处于 pending_approval 状态
        req = self._requests.get(request_id)
        if req is None:
            raise ValueError(f"Unknown plugin execution request_id: {request_id!r}")
        if req.state != PluginAuthState.PENDING_APPROVAL:
            raise ValueError(f"Cannot approve request '{request_id}' in state '{req.state.value}'.")
        if user_id is not None and user_id != req.user_id:
            raise PermissionError("Approving user_id does not match request user_id.")
        if tenant_id is not None and tenant_id != req.tenant_id:
            raise PermissionError("Approving tenant_id does not match request tenant_id.")

        # 第二步：更新请求状态为 authorized 并创建可信批准记录
        updated_req = req.model_copy(
            update={
                "state": PluginAuthState.AUTHORIZED,
                "run_id": run_id or req.run_id,
            }
        )
        self._requests[request_id] = updated_req
        record = PluginApprovalRecord(
            approval_id=f"appr-{uuid4().hex[:12]}",
            request_id=request_id,
            user_id=updated_req.user_id,
            tenant_id=updated_req.tenant_id,
            thread_id=updated_req.thread_id,
            plugin_id=updated_req.plugin_id,
            workflow_name=updated_req.workflow_name,
            input_scope=updated_req.input_scope,
            planned_outputs=updated_req.planned_outputs,
            allowed_effects=frozenset(updated_req.allowed_effects),
            run_id=updated_req.run_id,
            active=True,
        )
        self._approvals[record.approval_id] = record
        self._state_by_key[(req.thread_id, req.plugin_id)] = PluginAuthState.AUTHORIZED
        self._state_by_key[(req.thread_id, req.workflow_name)] = PluginAuthState.AUTHORIZED
        if self._remember_authorizations:
            self.authorize_plugin(req.plugin_id, user_id=req.user_id, tenant_id=req.tenant_id)
        return record

    def reject_request(
        self,
        request_id: str,
        *,
        reason: str = "User rejected plugin execution",
    ) -> PluginExecutionRequest:
        """由可信宿主调用：记录用户拒绝某次插件运行请求，状态推进至 `cancelled` 并阻断后续重试。

        :param request_id: 待拒绝的请求 ID。
        :type request_id: str
        :param reason: 拒绝原因说明。
        :type reason: str
        :return: 更新后的请求对象。
        :rtype: PluginExecutionRequest
        :raises ValueError: 当请求不存在时抛出。
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：获取请求并将状态置为 cancelled
        req = self._requests.get(request_id)
        if req is None:
            raise ValueError(f"Unknown plugin execution request_id: {request_id!r}")
        updated = req.model_copy(
            update={"state": PluginAuthState.CANCELLED, "rejection_reason": reason}
        )
        self._requests[request_id] = updated

        # 第二步：作废关联批准记录并锁定当前线程下的重试阻断标记
        for appr in self._approvals.values():
            if appr.request_id == request_id:
                appr.active = False
        self._rejected_keys.add((req.run_id or req.thread_id, req.plugin_id))
        self._rejected_keys.add((req.run_id or req.thread_id, req.workflow_name))
        self._state_by_key[(req.thread_id, req.plugin_id)] = PluginAuthState.CANCELLED
        self._state_by_key[(req.thread_id, req.workflow_name)] = PluginAuthState.CANCELLED
        return updated

    def _find_active_approval(
        self,
        *,
        plugin_or_workflow: str,
        thread_id: str,
        user_id: str,
        tenant_id: str,
    ) -> PluginApprovalRecord | None:
        """查找匹配身份、会话与插件/工作流的活跃批准记录。

        :param plugin_or_workflow: 插件 ID 或工作流名称。
        :type plugin_or_workflow: str
        :param thread_id: 会话线程 ID。
        :type thread_id: str
        :param user_id: 用户 ID。
        :type user_id: str
        :param tenant_id: 租户 ID。
        :type tenant_id: str
        :return: 匹配的 PluginApprovalRecord 或 None。
        :rtype: PluginApprovalRecord | None
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：倒序遍历批准记录，匹配活跃且身份一致的授权
        for record in reversed(list(self._approvals.values())):
            if not record.active:
                continue
            if record.thread_id != thread_id:
                continue
            if record.user_id != user_id or record.tenant_id != tenant_id:
                continue
            if plugin_or_workflow in {record.plugin_id, record.workflow_name}:
                return record
        return None

    def verify_execution(
        self,
        *,
        plugin_id: str,
        workflow_name: str,
        user_id: str,
        tenant_id: str,
        thread_id: str,
        run_id: str,
        execution_id: str | None = None,
        input_data: dict[str, Any] | None = None,
        effect: SideEffect = SideEffect.READ,
    ) -> None:
        """统一校验插件执行请求是否已获可信宿主授权（实现 `AuthorizationGuardProtocol`）。

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
        :return: 校验通过时无返回值。
        :rtype: None
        :raises PermissionError: 当含伪造参数、已被拒绝、未授权、超出数据范围或副作用越权时抛出。
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：阻断任何模型伪造的 approved=True / authorized=True 参数
        payload = _extract_payload_from_input(input_data)
        spoofed_key = _contains_spoofed_auth_flag(payload)
        if spoofed_key is not None:
            raise PermissionError(
                f"Spoofed authorization parameter '{spoofed_key}' in payload is forbidden; "
                "authorization must come from host PluginAuthorizationGateway."
            )

        # 第二步：若用户已拒绝该插件，立即终止并禁止重试
        if (run_id or thread_id, workflow_name) in self._rejected_keys or (
            run_id or thread_id,
            plugin_id,
        ) in self._rejected_keys:
            raise PermissionError(
                f"Plugin '{plugin_id}' ({workflow_name}) was rejected by user in thread '{thread_id}'; terminate without retrying."
            )

        if plugin_id not in self._enabled:
            raise PermissionError(f"Plugin '{plugin_id}' is not enabled by the host.")
        if (tenant_id, user_id, plugin_id) in self._authorized_plugins:
            return

        # 第三步：查找宿主维护的活跃批准记录
        record = self._find_active_approval(
            plugin_or_workflow=workflow_name or plugin_id,
            thread_id=thread_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )
        if record is None:
            raise PermissionError(
                f"Plugin '{plugin_id}' ({workflow_name}) is not authorized by user "
                f"(user={user_id!r}, tenant={tenant_id!r}, thread={thread_id!r})."
            )

        # 第四步：校验单次运行 ID 绑定（同一次授权可覆盖该次运行内的全部节点，但不可跨运行静默复用）
        if record.run_id is not None and run_id and record.run_id != run_id:
            raise PermissionError(
                f"Approval '{record.approval_id}' is bound to run_id={record.run_id!r}, "
                f"cannot be reused for run_id={run_id!r}."
            )

        # 第五步：校验副作用范围是否超出用户批准级别
        if effect not in record.allowed_effects and not (
            effect == SideEffect.READ and bool(record.allowed_effects)
        ):
            raise PermissionError(
                f"Requested side effect '{effect.value}' exceeds approved effects "
                f"{sorted(e.value for e in record.allowed_effects)}; re-authorization required."
            )

        # 第六步：校验全部请求文件均属于用户确认的输入或输出范围
        requested_files: list[str] = []
        if payload.get("file_path"):
            requested_files.append(_normalize_scope_item(str(payload["file_path"])))
        for ev_file in payload.get("evidence_files") or ():
            requested_files.append(_normalize_scope_item(str(ev_file)))
        allowed_set = set(record.input_scope) | set(record.planned_outputs)
        for req_file in requested_files:
            if req_file not in allowed_set:
                raise PermissionError(
                    f"Input data scope '{req_file}' expands beyond approved scope "
                    f"{list(record.input_scope)}; re-authorization required."
                )

        # 第七步：绑定当前 run_id 并将状态推进至 running
        if record.run_id is None and run_id:
            record.run_id = run_id
        req = self._requests.get(record.request_id)
        if req is not None and req.state == PluginAuthState.AUTHORIZED:
            self._requests[record.request_id] = req.model_copy(
                update={"state": PluginAuthState.RUNNING, "run_id": record.run_id}
            )
        self._state_by_key[(thread_id, record.plugin_id)] = PluginAuthState.RUNNING
        self._state_by_key[(thread_id, record.workflow_name)] = PluginAuthState.RUNNING
        if execution_id is not None:
            self._execution_requests[execution_id] = record.request_id

    def finish_execution(
        self,
        request_or_run_id: str,
        *,
        status: Literal["completed", "failed", "cancelled"] = "completed",
    ) -> None:
        """完成或终止单次已授权运行，推进状态至 `completed`/`failed`/`cancelled` 并消耗本次单任务授权。

        :param request_or_run_id: 请求 ID、批准 ID 或运行 ID。
        :type request_or_run_id: str
        :param status: 结束状态（completed、failed 或 cancelled）。
        :type status: typing.Literal['completed', 'failed', 'cancelled']
        :return: 无返回值。
        :rtype: None
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：映射结束状态枚举
        target_state = {
            "completed": PluginAuthState.COMPLETED,
            "failed": PluginAuthState.FAILED,
            "cancelled": PluginAuthState.CANCELLED,
        }[status]

        # 第二步：定位对应的批准记录并标记消耗完毕（一次授权默认仅覆盖本次指定任务）
        request_id = self._execution_requests.pop(request_or_run_id, request_or_run_id)
        for record in self._approvals.values():
            if request_id in {record.request_id, record.approval_id, record.run_id}:
                selection = self._selected_by_thread.get(record.thread_id)
                same_selected_run = (
                    selection is not None
                    and selection[0] == record.run_id
                    and record.run_id is not None
                )
                if not same_selected_run:
                    record.active = False
                req = self._requests.get(record.request_id)
                if req is not None:
                    self._requests[record.request_id] = req.model_copy(
                        update={"state": target_state}
                    )
                self._state_by_key[(record.thread_id, record.plugin_id)] = target_state
                self._state_by_key[(record.thread_id, record.workflow_name)] = target_state


def _idempotency_key(subagent_type: str, description: str) -> str:
    """计算 (工作流名称, 任务描述) 的确定性 SHA-256 幂等摘要键。

    :param subagent_type: 子代理/工作流名称。
    :type subagent_type: str
    :param description: 任务描述字符串。
    :type description: str
    :return: 64 位 SHA-256 十六进制哈希串。
    :rtype: str
    :author: tongliga
    :date: 2026-10-19
    """
    # 第一步：拼装子代理类型与任务描述并计算 SHA-256 摘要
    payload = f"{subagent_type}::{description.strip()}".encode()
    return hashlib.sha256(payload).hexdigest()


def _extract_tool_message(
    result: ToolMessage | Command[Any],
    tool_call_id: str | None,
) -> ToolMessage | None:
    """从工具执行返回值（ToolMessage 或 LangGraph Command）中提取对应的 ToolMessage。

    :param result: 工具执行结果对象。
    :type result: langchain_core.messages.ToolMessage | langgraph.types.Command[typing.Any]
    :param tool_call_id: 工具调用唯一标识 ID。
    :type tool_call_id: str | None
    :return: 匹配到的 ToolMessage 实例，未找到返回 None。
    :rtype: langchain_core.messages.ToolMessage | None
    :author: tongliga
    :date: 2026-10-19
    """
    # 第一步：若直接为 ToolMessage 实例，直接返回
    if isinstance(result, ToolMessage):
        return result
    # 第二步：若是 Command，则解析 update["messages"] 遍历匹配对应 ID
    if isinstance(result, Command) and isinstance(result.update, dict):
        messages = result.update.get("messages")
        if isinstance(messages, list):
            for msg in messages:
                if isinstance(msg, ToolMessage) and (
                    tool_call_id is None or msg.tool_call_id == tool_call_id
                ):
                    return msg
    return None


def _inspect_history(
    messages: list[AnyMessage],
) -> tuple[Counter[str], set[str], dict[str, str]]:
    """从当前检查点对话历史中还原已委派次数、已完成工作流集合与幂等结果缓存。

    :param messages: 当前检查点的全量消息列表。
    :type messages: list[langchain_core.messages.AnyMessage]
    :return: (已委派次数计数器, 已完成工作流集合, 幂等输出缓存字典)。
    :rtype: tuple[collections.Counter[str], set[str], dict[str, str]]
    :author: tongliga
    :date: 2026-10-19
    """
    # 第一步：遍历历史消息，收集所有已发起的调度工具调用元数据
    call_meta: dict[str, tuple[str, str, str]] = {}
    for msg in messages:
        if not isinstance(msg, AIMessage) or not msg.tool_calls:
            continue
        for tc in msg.tool_calls:
            if tc.get("name") not in _DISPATCH_TOOLS:
                continue
            tc_id = tc.get("id")
            args = tc.get("args") or {}
            subagent_type = args.get("subagent_type")
            description = args.get("description", "")
            if isinstance(tc_id, str) and isinstance(subagent_type, str):
                call_meta[tc_id] = (
                    subagent_type,
                    description if isinstance(description, str) else str(description),
                    tc.get("name", "task"),
                )

    dispatch_counts: Counter[str] = Counter()
    completed_workflows: set[str] = set()
    idempotent_cache: dict[str, str] = {}

    # 第二步：遍历工具响应消息，审计成功与失败状态，构建完成集合与幂等缓存
    for msg in messages:
        if not isinstance(msg, ToolMessage) or msg.tool_call_id not in call_meta:
            continue
        subagent_type, description, tool_name = call_meta[msg.tool_call_id]
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        policy_meta = (
            (msg.artifact or {}).get("supervisor_policy", {})
            if isinstance(msg.artifact, dict)
            else {}
        )
        if policy_meta.get("dispatched") is False:
            continue
        if not policy_meta and content.startswith("[SupervisorPolicy]"):
            continue
        dispatch_counts[subagent_type] += 1
        is_error = getattr(msg, "status", None) == "error" or content.startswith(
            ("We cannot invoke subagent", "Failed to launch async subagent", "[SupervisorPolicy]")
        )
        if not is_error and tool_name == "task":
            completed_workflows.add(subagent_type)
            idempotent_cache[_idempotency_key(subagent_type, description)] = content

    return dispatch_counts, completed_workflows, idempotent_cache


def _normalize_workflow_map(
    workflows: Mapping[str, WorkflowContribution] | Sequence[WorkflowContribution],
) -> dict[str, WorkflowContribution]:
    """将工作流字典或序列统一规范化为 {name: WorkflowContribution} 字典。

    :param workflows: 工作流映射或序列。
    :type workflows: collections.abc.Mapping[str, vertical_plugins.common.contracts.WorkflowContribution] | collections.abc.Sequence[vertical_plugins.common.contracts.WorkflowContribution]
    :return: 规范化后的工作流字典。
    :rtype: dict[str, vertical_plugins.common.contracts.WorkflowContribution]
    :author: tongliga
    :date: 2026-10-19
    """
    # 第一步：区分 Mapping 与 Sequence 并统一转为字典映射
    if isinstance(workflows, Mapping):
        return dict(workflows)
    return {wf.name: wf for wf in workflows}


def _make_workflow_interrupt_predicate(allowed: frozenset[str]) -> Any:
    """构造同时兼容 ToolInterruptPredicate(tool_call, state, runtime) 与 ToolCallRequest 的审批谓词。

    :param allowed: 需触发审批拦截的工作流名称集合。
    :type allowed: frozenset[str]
    :return: 审批拦截判定谓词函数。
    :rtype: typing.Any
    :author: tongliga
    :date: 2026-10-19
    """

    # 第一步：构造并返回兼顾不同调用签名的断言判定函数
    def _predicate(first: Any, *_rest: Any) -> bool:
        tool_call = getattr(first, "tool_call", first)
        if not isinstance(tool_call, Mapping):
            return False
        args = tool_call.get("args")
        return isinstance(args, Mapping) and args.get("subagent_type") in allowed

    return _predicate


def build_workflow_interrupt_configs(
    workflows: Mapping[str, WorkflowContribution] | Sequence[WorkflowContribution],
    *,
    preferred_mode: WorkflowMode | str | None = None,
) -> dict[str, InterruptOnConfig]:
    """为高风险（WRITE / EXTERNAL）工作流构造按 `subagent_type` 条件触发的 HITL 审批配置。

    :param workflows: 已注册的工作流贡献字典或序列。
    :type workflows: collections.abc.Mapping[str, vertical_plugins.common.contracts.WorkflowContribution] | collections.abc.Sequence[vertical_plugins.common.contracts.WorkflowContribution]
    :param preferred_mode: 宿主偏好的工作流运行模式。
    :type preferred_mode: vertical_plugins.common.contracts.WorkflowMode | str | None
    :return: 工具名称（'task' 或 'start_async_task'）到 InterruptOnConfig 的映射。
    :rtype: dict[str, langchain.agents.middleware.InterruptOnConfig]
    :author: tongliga
    :date: 2026-10-19
    """
    # 第一步：规范化工作流映射并筛选需要审批的工作流名称
    workflow_map = _normalize_workflow_map(workflows)
    approval_names = frozenset(wf.name for wf in workflow_map.values() if wf.requires_approval)
    if not approval_names:
        return {}

    # 第二步：按 preferred_mode 分别筛选同步与异步审批工作流集合
    compiled_approval_names = (
        frozenset(
            wf.name
            for wf in workflow_map.values()
            if wf.requires_approval and wf.resolve_mode(preferred_mode) == WorkflowMode.COMPILED
        )
        if preferred_mode is not None
        else approval_names
    )
    async_approval_names = (
        frozenset(
            wf.name
            for wf in workflow_map.values()
            if wf.requires_approval and wf.resolve_mode(preferred_mode) == WorkflowMode.ASYNC
        )
        if preferred_mode is not None
        else approval_names
    )

    # 第三步：构造 InterruptOnConfig 配置字典
    configs: dict[str, InterruptOnConfig] = {}
    if compiled_approval_names:
        configs["task"] = InterruptOnConfig(
            allowed_decisions=["approve", "reject"],
            when=_make_workflow_interrupt_predicate(compiled_approval_names),
        )
    if async_approval_names:
        configs["start_async_task"] = InterruptOnConfig(
            allowed_decisions=["approve", "reject"],
            when=_make_workflow_interrupt_predicate(async_approval_names),
        )
    return configs


class SupervisorPolicyMiddleware(AgentMiddleware[Any, RunContext, Any]):
    """确定性监督策略中间件：在不引入额外 LLM 的前提下约束 Deep Agents Supervisor 的任务调度。

    负责执行：
    1. 插件工作流调用白名单、租户权限（RBAC）与 `PluginAuthorizationGateway` 用户授权校验；
    2. 拦截模型伪造的 `approved=True` / `authorized=True` 参数；
    3. 工作流前置依赖关系（`depends_on`）拓扑校验；
    4. 最大委派/重试次数（`max_dispatches`）限制与用户拒绝后立即终止；
    5. 相同任务载荷的幂等复用（`idempotent`）；
    6. 工作流返回结果的 Pydantic 模式确定性验收（`result_schema`）。
    """

    def __init__(
        self,
        workflows: Mapping[str, WorkflowContribution] | Sequence[WorkflowContribution] = (),
        *,
        allowed_subagents: frozenset[str] | None = None,
        enforce_allowlist: bool = True,
        blocked_tools: frozenset[str] = frozenset(),
        auth_gateway: PluginAuthorizationGateway | None = None,
    ) -> None:
        """初始化 Supervisor 确定性策略中间件。

        :param workflows: 工作流名称到 WorkflowContribution 的映射或工作流贡献序列。
        :type workflows: collections.abc.Mapping[str, vertical_plugins.common.contracts.WorkflowContribution] | collections.abc.Sequence[vertical_plugins.common.contracts.WorkflowContribution]
        :param allowed_subagents: 允许调用的全部子智能体名称集合。
        :type allowed_subagents: frozenset[str] | None
        :param enforce_allowlist: 是否严格拦截未在白名单中显式登记的子智能体。
        :type enforce_allowlist: bool
        :param blocked_tools: 显式封禁的工具名称集合。
        :type blocked_tools: frozenset[str]
        :param auth_gateway: 可选绑定的宿主统一插件授权网关。
        :type auth_gateway: PluginAuthorizationGateway | None
        :return: 无返回值。
        :rtype: None
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：调用基类中间件构造函数
        super().__init__()
        # 第二步：规整化工作流映射表、受限白名单集合与授权网关
        self._workflows = _normalize_workflow_map(workflows)
        self._allowed_subagents = (
            frozenset(allowed_subagents)
            if allowed_subagents is not None
            else frozenset(self._workflows)
        )
        self._enforce_allowlist = enforce_allowlist
        self._blocked_tools = blocked_tools
        self._auth_gateway = auth_gateway

    def wrap_model_call(
        self, request: ModelRequest, handler: Callable[..., ModelResponse]
    ) -> ModelResponse:
        """同步拦截模型请求，过滤受限工具能力。

        :param request: 模型调用请求对象。
        :type request: langchain.agents.middleware.ModelRequest
        :param handler: 下游调用处理器函数。
        :type handler: collections.abc.Callable[..., langchain.agents.middleware.ModelResponse]
        :return: 模型响应对象。
        :rtype: langchain.agents.middleware.ModelResponse
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：过滤封禁工具列表并转发下游处理器
        return handler(
            request.override(
                tools=[
                    t
                    for t in request.tools
                    if (t.get("name") if isinstance(t, dict) else t.name) not in self._blocked_tools
                ]
            )
        )

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[..., Awaitable[ModelResponse]],
    ) -> ModelResponse:
        """异步拦截模型请求，过滤受限工具能力。

        :param request: 模型调用请求对象。
        :type request: langchain.agents.middleware.ModelRequest
        :param handler: 下游异步调用处理器函数。
        :type handler: collections.abc.Callable[..., collections.abc.Awaitable[langchain.agents.middleware.ModelResponse]]
        :return: 模型响应对象。
        :rtype: langchain.agents.middleware.ModelResponse
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：过滤封禁工具列表并转发下游异步处理器
        return await handler(
            request.override(
                tools=[
                    t
                    for t in request.tools
                    if (t.get("name") if isinstance(t, dict) else t.name) not in self._blocked_tools
                ]
            )
        )

    def _pre_dispatch_check(
        self,
        request: ToolCallRequest,
    ) -> ToolMessage | None:
        """在委派工作流前执行确定性策略与授权网关检查；若拒绝或命中幂等缓存则直接返回 ToolMessage。

        :param request: 工具调用请求对象。
        :type request: langchain.agents.middleware.types.ToolCallRequest
        :return: 若策略拦截或命中幂等缓存则返回 ToolMessage，放行则返回 None。
        :rtype: langchain_core.messages.ToolMessage | None
        :author: tongliga
        :date: 2026-10-19
        """
        tool_name = request.tool_call.get("name", "")
        tool_call_id = request.tool_call.get("id") or "unknown"
        raw_args = request.tool_call.get("args")
        args_dict = raw_args if isinstance(raw_args, dict) else {}

        # 第一步：检查工具是否属于受封禁工具
        if tool_name in self._blocked_tools:
            return ToolMessage(
                content="[SupervisorPolicy] Direct tool execution is disabled.",
                tool_call_id=tool_call_id,
                status="error",
            )

        # 第二步：无条件拦截模型在任何工具调用参数中伪造的 approved=True / authorized=True 字段
        extracted_payload = _extract_payload_from_input(args_dict)
        spoofed_key = _contains_spoofed_auth_flag(extracted_payload)
        if spoofed_key is not None:
            return ToolMessage(
                content=(
                    f"[SupervisorPolicy] Spoofed authorization field '{spoofed_key}' in tool arguments "
                    "is strictly prohibited. Authorization must come from the host gateway."
                ),
                tool_call_id=tool_call_id,
                status="error",
            )

        # 第三步：审计异步子任务监控类工具权限
        if tool_name in _MONITOR_TOOLS:
            if tool_name == "update_async_task":
                return ToolMessage(
                    content="[SupervisorPolicy] Async updates require a new approved dispatch.",
                    tool_call_id=tool_call_id,
                    status="error",
                )
            if tool_name != "list_async_tasks":
                task = (request.state.get("async_tasks") or {}).get(args_dict.get("task_id"))
                workflow = self._workflows.get(task.get("agent_name")) if task else None
                tenant = getattr(getattr(request.runtime, "context", None), "tenant_id", None)
                if workflow is None or (
                    workflow.allowed_tenants is not None and tenant not in workflow.allowed_tenants
                ):
                    return ToolMessage(
                        content="[SupervisorPolicy] Async task is unknown or unauthorized.",
                        tool_call_id=tool_call_id,
                        status="error",
                    )
            return None

        # 第四步：若非任务派发工具，直接放行通用能力工具
        if tool_name not in _DISPATCH_TOOLS:
            return None

        if not isinstance(raw_args, dict):
            return None

        subagent_type = str(raw_args.get("subagent_type", ""))
        description = str(raw_args.get("description", ""))

        # 第五步：检查子代理白名单约束
        if self._enforce_allowlist and subagent_type not in self._allowed_subagents:
            allowed_list = ", ".join(sorted(self._allowed_subagents)) or "none"
            return ToolMessage(
                content=(
                    f"[SupervisorPolicy] Workflow '{subagent_type}' is not in the registered "
                    f"plugin allowlist. Allowed workflows: {allowed_list}."
                ),
                tool_call_id=tool_call_id,
                status="error",
            )

        workflow = self._workflows.get(subagent_type)
        if workflow is None:
            return None

        if tool_name == "start_async_task" and (
            workflow.depends_on
            or workflow.idempotent
            or workflow.result_schema is not None
            or any(workflow.name in wf.depends_on for wf in self._workflows.values())
        ):
            return ToolMessage(
                content="[SupervisorPolicy] Async dependency/idempotency/result contracts are not supported; use a compiled workflow.",
                tool_call_id=tool_call_id,
                status="error",
            )

        # 第六步：校验租户访问权限（RBAC）
        context = getattr(request.runtime, "context", None)
        tenant_id = getattr(context, "tenant_id", "default") or "default"
        user_id = getattr(context, "user_id", "default") or "default"
        cfg = (
            getattr(request, "config", None)
            or getattr(getattr(request, "runtime", None), "config", None)
            or {}
        )
        cfg_dict = cfg.get("configurable", {}) if isinstance(cfg, dict) else {}
        thread_id = str(
            cfg_dict.get("thread_id") or (request.state or {}).get("thread_id") or "default"
        )

        if workflow.allowed_tenants is not None and tenant_id not in workflow.allowed_tenants:
            return ToolMessage(
                content=(
                    f"[SupervisorPolicy] Tenant is not authorized to dispatch workflow "
                    f"'{subagent_type}'."
                ),
                tool_call_id=tool_call_id,
                status="error",
            )

        # 第七步：若绑定了 PluginAuthorizationGateway，强制执行统一授权网关校验
        if self._auth_gateway is not None:
            plugin_id = self._auth_gateway._workflow_to_plugin.get(subagent_type, subagent_type)
            turn_id = self._auth_gateway._selected_by_thread.get(thread_id, (None, frozenset()))[0]
            try:
                self._auth_gateway.verify_execution(
                    plugin_id=plugin_id,
                    workflow_name=subagent_type,
                    user_id=user_id,
                    tenant_id=tenant_id,
                    thread_id=thread_id,
                    run_id=turn_id,
                    execution_id=tool_call_id,
                    input_data=raw_args,
                    effect=workflow.effect,
                )
            except PermissionError as exc:
                err_text = str(exc)
                if "rejected by user" in err_text:
                    return ToolMessage(
                        content=f"[SupervisorPolicy] {err_text}",
                        tool_call_id=tool_call_id,
                        status="error",
                    )
                # 自动登记为 pending_approval 请求并返回确认详情供宿主呈现给用户
                scope_items: list[str] = []
                if extracted_payload.get("file_path"):
                    scope_items.append(str(extracted_payload["file_path"]))
                scope_items.extend(
                    str(item) for item in extracted_payload.get("evidence_files") or ()
                )
                req = self._auth_gateway.request_execution(
                    plugin_id=plugin_id,
                    workflow_name=subagent_type,
                    purpose=str(
                        extracted_payload.get("research_question")
                        or extracted_payload.get("analysis_goal")
                        or description[:120]
                        or f"Run {subagent_type}"
                    ),
                    input_scope=scope_items,
                    allowed_effects=(SideEffect.READ, workflow.effect),
                    user_id=user_id,
                    tenant_id=tenant_id,
                    thread_id=thread_id,
                    run_id=turn_id,
                )
                answer = interrupt(
                    {
                        "kind": "plugin_authorization",
                        "request_id": req.request_id,
                        "plugin_id": plugin_id,
                        "workflow_name": subagent_type,
                        "purpose": req.purpose,
                        "input_scope": list(req.input_scope),
                        "planned_outputs": list(req.planned_outputs),
                        "allowed_effects": [effect.value for effect in req.allowed_effects],
                        "question": req.format_user_prompt(),
                    }
                )
                if not isinstance(answer, dict) or set(answer) != {"request_id", "approved"}:
                    raise ValueError(
                        "Plugin authorization response must contain request_id and approved"
                    ) from None
                if answer["request_id"] != req.request_id or not isinstance(
                    answer["approved"], bool
                ):
                    raise ValueError(
                        "Plugin authorization response does not match the pending request"
                    ) from None
                if answer["approved"]:
                    self._auth_gateway.approve_request(
                        req.request_id,
                        user_id=user_id,
                        tenant_id=tenant_id,
                        run_id=turn_id,
                    )
                else:
                    self._auth_gateway.reject_request(req.request_id)
                    return ToolMessage(
                        content="[SupervisorPolicy] Plugin execution was rejected by the user.",
                        tool_call_id=tool_call_id,
                        status="error",
                    )

        messages = list((request.state or {}).get("messages") or [])
        _, completed_workflows, idempotent_cache = _inspect_history(messages)
        current_turn_start = next(
            (
                index
                for index in range(len(messages) - 1, -1, -1)
                if isinstance(messages[index], HumanMessage)
            ),
            0,
        )
        current_turn_dispatch_counts = _inspect_history(messages[current_turn_start:])[0]

        # 第八步：校验前置工作流依赖关系（depends_on）
        missing_deps = [dep for dep in workflow.depends_on if dep not in completed_workflows]
        if missing_deps:
            return ToolMessage(
                content=(
                    f"[SupervisorPolicy] Workflow '{subagent_type}' requires completed "
                    f"prerequisite workflows: {', '.join(missing_deps)}."
                ),
                tool_call_id=tool_call_id,
                status="error",
            )

        # 第九步：校验幂等缓存
        if workflow.idempotent and tool_name == "task":
            idem_key = _idempotency_key(subagent_type, description)
            if idem_key in idempotent_cache:
                cached_output = idempotent_cache[idem_key]
                return ToolMessage(
                    content=cached_output,
                    tool_call_id=tool_call_id,
                    artifact={"supervisor_policy": {"dispatched": False}},
                )

        # 第十步：同一模型批次调用预占与重复排重
        answered = {m.tool_call_id for m in messages if isinstance(m, ToolMessage)}
        batch = next(
            (
                m
                for m in reversed(messages)
                if isinstance(m, AIMessage)
                and any(c.get("id") == tool_call_id for c in m.tool_calls)
            ),
            None,
        )
        if batch and sum(c.get("id") == tool_call_id for c in batch.tool_calls) != 1:
            return ToolMessage(
                content="[SupervisorPolicy] Tool call IDs must be unique within a batch.",
                tool_call_id=tool_call_id,
                status="error",
            )
        reserved = 0
        for call in batch.tool_calls if batch else ():
            if call.get("id") == tool_call_id:
                break
            prior = call.get("args") or {}
            if (
                call.get("name") not in _DISPATCH_TOOLS
                or prior.get("subagent_type") != subagent_type
            ):
                continue
            if (
                workflow.idempotent
                and tool_name == "task"
                and _idempotency_key(subagent_type, str(prior.get("description", "")))
                == _idempotency_key(subagent_type, description)
            ):
                return ToolMessage(
                    content="[SupervisorPolicy] Duplicate task in the same batch; retry after the first result.",
                    tool_call_id=tool_call_id,
                    status="error",
                )
            if (
                workflow.idempotent
                and _idempotency_key(subagent_type, str(prior.get("description", "")))
                in idempotent_cache
            ):
                continue
            if call.get("id") not in answered:
                reserved += 1

        # 第十一步：校验最大委派/重试次数上限（max_dispatches）
        if current_turn_dispatch_counts[subagent_type] + reserved >= workflow.max_dispatches:
            return ToolMessage(
                content=(
                    f"[SupervisorPolicy] Workflow '{subagent_type}' exceeded "
                    f"max_dispatches={workflow.max_dispatches}."
                ),
                tool_call_id=tool_call_id,
                status="error",
            )

        return None

    def _post_dispatch_validate(
        self,
        request: ToolCallRequest,
        result: ToolMessage | Command[Any],
    ) -> ToolMessage | Command[Any]:
        """在同步工作流返回后执行确定性输出模式验收（Validate 阶段）。

        :param request: 工具调用请求对象。
        :type request: langchain.agents.middleware.types.ToolCallRequest
        :param result: 工具执行输出对象。
        :type result: langchain_core.messages.ToolMessage | langgraph.types.Command[typing.Any]
        :return: 模式校验通过的原结果或验收失败的错误 ToolMessage。
        :rtype: langchain_core.messages.ToolMessage | langgraph.types.Command[typing.Any]
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：仅对同步 task 工作流调用进行模式验收
        tool_name = request.tool_call.get("name", "")
        if tool_name != "task":
            return result

        args = request.tool_call.get("args")
        if not isinstance(args, dict):
            return result
        subagent_type = str(args.get("subagent_type", ""))
        workflow = self._workflows.get(subagent_type)
        if workflow is None:
            return result

        # 第二步：获取该工作流要求的结果模式规范
        schema: type[BaseModel] | None = workflow.result_schema
        if (
            schema is None
            and isinstance(workflow.output_schema, type)
            and issubclass(workflow.output_schema, BaseModel)
        ):
            schema = workflow.output_schema
        if schema is None:
            return result

        # 第三步：提取输出 ToolMessage 并执行 Pydantic 校验
        tool_call_id = request.tool_call.get("id")
        tool_msg = _extract_tool_message(result, tool_call_id)
        if tool_msg is None or tool_msg.status == "error":
            return result

        content = tool_msg.content if isinstance(tool_msg.content, str) else str(tool_msg.content)
        try:
            schema.model_validate_json(content)
        except ValidationError as exc:
            logger.warning(
                "supervisor_policy_validation_failed",
                extra={
                    "event_fields": {
                        "supervisor_stage": SupervisorStage.VALIDATE.value,
                        "subagent_name": subagent_type,
                        "error_count": len(exc.errors()),
                    }
                },
            )
            error_msg = ToolMessage(
                content=(
                    f"[SupervisorPolicy] Workflow '{subagent_type}' output failed result schema "
                    f"validation ({schema.__name__})."
                ),
                tool_call_id=tool_msg.tool_call_id,
                status="error",
            )
            if isinstance(result, Command) and isinstance(result.update, dict):
                return replace(
                    result,
                    update={
                        **result.update,
                        "messages": [
                            error_msg if m is tool_msg else m
                            for m in result.update.get("messages", [])
                        ],
                    },
                )
            return error_msg

        return result

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        """同步拦截并监督工作流委派与结果验收。

        :param request: 工具调用请求对象。
        :type request: langchain.agents.middleware.types.ToolCallRequest
        :param handler: 下游工具调用执行处理器函数。
        :type handler: collections.abc.Callable[[langchain.agents.middleware.types.ToolCallRequest], langchain_core.messages.ToolMessage | langgraph.types.Command[typing.Any]]
        :return: 工具调用输出结果。
        :rtype: langchain_core.messages.ToolMessage | langgraph.types.Command[typing.Any]
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：执行委派前确定性策略检查
        short_circuit = self._pre_dispatch_check(request)
        if short_circuit is not None:
            return short_circuit.model_copy(
                update={"artifact": {"supervisor_policy": {"dispatched": False}}}
            )
        # 第二步：调用下游处理器执行工具
        result = handler(request)
        # 第三步：执行后置输出模式验收与委派元数据标记
        return self._mark_dispatch(request, self._post_dispatch_validate(request, result))

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        """异步拦截并监督工作流委派与结果验收。

        :param request: 工具调用请求对象。
        :type request: langchain.agents.middleware.types.ToolCallRequest
        :param handler: 下游异步工具调用执行处理器函数。
        :type handler: collections.abc.Callable[[langchain.agents.middleware.types.ToolCallRequest], collections.abc.Awaitable[langchain_core.messages.ToolMessage | langgraph.types.Command[typing.Any]]]
        :return: 工具调用输出结果。
        :rtype: langchain_core.messages.ToolMessage | langgraph.types.Command[typing.Any]
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：执行委派前确定性策略检查
        short_circuit = self._pre_dispatch_check(request)
        if short_circuit is not None:
            return short_circuit.model_copy(
                update={"artifact": {"supervisor_policy": {"dispatched": False}}}
            )
        # 第二步：调用下游异步处理器执行工具
        result = await handler(request)
        # 第三步：执行后置输出模式验收与委派元数据标记
        return self._mark_dispatch(request, self._post_dispatch_validate(request, result))

    def _mark_dispatch(
        self, request: ToolCallRequest, result: ToolMessage | Command[Any]
    ) -> ToolMessage | Command[Any]:
        """为通过委派的工作流调用结果标记 dispatched=True 元数据，并同步更新授权网关完成状态。

        :param request: 原始工具调用请求对象。
        :type request: langchain.agents.middleware.types.ToolCallRequest
        :param result: 工具执行输出对象。
        :type result: langchain_core.messages.ToolMessage | langgraph.types.Command[typing.Any]
        :return: 注入元数据标记后的更新结果对象。
        :rtype: langchain_core.messages.ToolMessage | langgraph.types.Command[typing.Any]
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：若非任务委派工具直接放行
        if request.tool_call.get("name") not in _DISPATCH_TOOLS:
            return result
        # 第二步：提取 ToolMessage 并同步授权网关完成状态
        tool_call_id = str(request.tool_call.get("id") or "")
        msg = _extract_tool_message(result, tool_call_id)
        if msg is None:
            return result
        if self._auth_gateway is not None and request.tool_call.get("name") == "task":
            is_err = getattr(msg, "status", None) == "error"
            self._auth_gateway.finish_execution(
                tool_call_id,
                status="failed" if is_err else "completed",
            )
        artifact = dict(msg.artifact) if isinstance(msg.artifact, dict) else {}
        artifact["supervisor_policy"] = {
            **artifact.get("supervisor_policy", {}),
            "dispatched": True,
        }
        updated = msg.model_copy(update={"artifact": artifact})
        if isinstance(result, ToolMessage):
            return updated
        if isinstance(result, Command) and isinstance(result.update, dict):
            return replace(
                result,
                update={
                    **result.update,
                    "messages": [
                        updated if m is msg else m for m in result.update.get("messages", [])
                    ],
                },
            )
        return result


__all__ = [
    "PluginApprovalRecord",
    "PluginAuthState",
    "PluginAuthorizationGateway",
    "PluginExecutionRequest",
    "SupervisorPolicyMiddleware",
    "SupervisorStage",
    "build_workflow_interrupt_configs",
]
