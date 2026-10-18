"""Supervisor 确定性监督策略中间件：管控工作流白名单、依赖顺序、重试上限、幂等性与结果验收。"""

from __future__ import annotations

import hashlib
import logging
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import replace
from enum import StrEnum
from typing import Any

from langchain.agents.middleware import (
    AgentMiddleware,
    InterruptOnConfig,
    ModelRequest,
    ModelResponse,
)
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import AIMessage, AnyMessage, ToolMessage
from langgraph.types import Command
from pydantic import BaseModel, ValidationError

from vertical_harness.plugins.contracts import (
    RunContext,
    WorkflowContribution,
    WorkflowMode,
)

logger = logging.getLogger(__name__)

# Deep Agents 同步与异步子任务委派工具名称集合
_DISPATCH_TOOLS = frozenset({"task", "start_async_task"})
# Deep Agents 异步子任务状态监控与管理生命周期工具名称集合
_MONITOR_TOOLS = frozenset(
    {"check_async_task", "update_async_task", "cancel_async_task", "list_async_tasks"}
)


class SupervisorStage(StrEnum):
    """主智能体（Supervisor）监督工作流生命周期阶段枚举。"""

    PLAN = "plan"
    DISPATCH = "dispatch"
    MONITOR = "monitor"
    VALIDATE = "validate"
    FINALIZE = "finalize"


def _idempotency_key(subagent_type: str, description: str) -> str:
    """计算 (工作流名称, 任务描述) 的确定性 SHA-256 幂等摘要键。"""
    logger.debug(
        "calculating_idempotency_key",
        extra={"event_fields": {"subagent_type": subagent_type}},
    )
    payload = f"{subagent_type}::{description.strip()}".encode()
    return hashlib.sha256(payload).hexdigest()


def _extract_tool_message(
    result: ToolMessage | Command[Any],
    tool_call_id: str | None,
) -> ToolMessage | None:
    """从工具执行返回值（ToolMessage 或 LangGraph Command）中提取对应的 ToolMessage。"""
    logger.debug("extracting_tool_message")
    if isinstance(result, ToolMessage):
        return result
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

    通过直接从 `state["messages"]` 派生历史状态，保证在执行 Time Travel 或 Checkpoint Fork
    回溯到任意历史节点时，监督策略状态与检查点严格一致。
    """
    logger.debug("inspecting_supervisor_history_started")
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

    logger.debug(
        "inspecting_supervisor_history_completed",
        extra={
            "event_fields": {
                "completed_workflows_count": len(completed_workflows),
                "idempotent_cached_count": len(idempotent_cache),
            }
        },
    )
    return dispatch_counts, completed_workflows, idempotent_cache


def _normalize_workflow_map(
    workflows: Mapping[str, WorkflowContribution] | Sequence[WorkflowContribution],
) -> dict[str, WorkflowContribution]:
    """将工作流字典或序列统一规范化为 {name: WorkflowContribution} 字典。"""
    logger.debug("normalizing_workflow_map")
    if isinstance(workflows, Mapping):
        return dict(workflows)
    return {wf.name: wf for wf in workflows}


def _make_workflow_interrupt_predicate(allowed: frozenset[str]) -> Any:
    """构造同时兼容 ToolInterruptPredicate(tool_call, state, runtime) 与 ToolCallRequest 的审批谓词。"""
    logger.debug("creating_workflow_interrupt_predicate")

    def _predicate(first: Any, *_rest: Any) -> bool:
        logger.debug("evaluating_workflow_interrupt_predicate")
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

    利用 LangChain `InterruptOnConfig(when=...)` 谓词，仅在主智能体委派需要审批的工作流时挂起，
    纯只读工作流自动放行。

    :param workflows: 已注册的工作流贡献字典或序列。
    :param preferred_mode: 宿主偏好的工作流运行模式。
    :return: 工具名称（'task' 或 'start_async_task'）到 InterruptOnConfig 的映射。
    """
    workflow_map = _normalize_workflow_map(workflows)
    approval_names = frozenset(wf.name for wf in workflow_map.values() if wf.requires_approval)
    if not approval_names:
        return {}

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
    1. 插件工作流调用白名单与租户权限（RBAC）校验；
    2. 工作流前置依赖关系（`depends_on`）拓扑校验；
    3. 最大委派/重试次数（`max_dispatches`）限制；
    4. 相同任务载荷的幂等复用（`idempotent`）；
    5. 工作流返回结果的 Pydantic 模式确定性验收（`result_schema`）。
    """

    def __init__(
        self,
        workflows: Mapping[str, WorkflowContribution] | Sequence[WorkflowContribution] = (),
        *,
        allowed_subagents: frozenset[str] | None = None,
        enforce_allowlist: bool = True,
        blocked_tools: frozenset[str] = frozenset(),
    ) -> None:
        """初始化 Supervisor 确定性策略中间件。

        :param workflows: 工作流名称到 WorkflowContribution 的映射或工作流贡献序列。
        :param allowed_subagents: 允许调用的全部子智能体名称集合（包含声明式子智能体与工作流）。
        :param enforce_allowlist: 是否严格拦截未在白名单中显式登记的子智能体（含默认 general-purpose）。
        """
        super().__init__()
        self._workflows = _normalize_workflow_map(workflows)
        self._allowed_subagents = (
            frozenset(allowed_subagents)
            if allowed_subagents is not None
            else frozenset(self._workflows)
        )
        self._enforce_allowlist = enforce_allowlist
        self._blocked_tools = blocked_tools

    def wrap_model_call(
        self, request: ModelRequest, handler: Callable[..., ModelResponse]
    ) -> ModelResponse:
        """同步拦截模型请求，过滤受限工具能力。

        :param request: 模型调用请求对象。
        :param handler: 下游调用处理器函数。
        :return: 模型响应对象。
        """
        logger.debug("supervisor_wrapping_model_call_sync")
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
        :param handler: 下游异步调用处理器函数。
        :return: 模型响应对象。
        """
        logger.debug("supervisor_wrapping_model_call_async")
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
        """在委派工作流前执行确定性策略检查；若拒绝或命中幂等缓存则直接返回 ToolMessage。"""
        tool_name = request.tool_call.get("name", "")
        if tool_name in self._blocked_tools:
            return ToolMessage(
                content="[SupervisorPolicy] Direct tool execution is disabled.",
                tool_call_id=request.tool_call["id"],
                status="error",
            )
        if tool_name in _MONITOR_TOOLS:
            if tool_name == "update_async_task":
                return ToolMessage(
                    content="[SupervisorPolicy] Async updates require a new approved dispatch.",
                    tool_call_id=request.tool_call["id"],
                    status="error",
                )
            if tool_name != "list_async_tasks":
                args = request.tool_call.get("args") or {}
                task = (request.state.get("async_tasks") or {}).get(args.get("task_id"))
                workflow = self._workflows.get(task.get("agent_name")) if task else None
                tenant = getattr(getattr(request.runtime, "context", None), "tenant_id", None)
                if workflow is None or (
                    workflow.allowed_tenants is not None and tenant not in workflow.allowed_tenants
                ):
                    return ToolMessage(
                        content="[SupervisorPolicy] Async task is unknown or unauthorized.",
                        tool_call_id=request.tool_call["id"],
                        status="error",
                    )
            logger.debug(
                "supervisor_workflow_monitored",
                extra={
                    "event_fields": {
                        "supervisor_stage": SupervisorStage.MONITOR.value,
                        "tool_name": tool_name,
                    }
                },
            )
            return None

        if tool_name not in _DISPATCH_TOOLS:
            return None

        tool_call_id = request.tool_call.get("id") or "unknown"
        args = request.tool_call.get("args")
        if not isinstance(args, dict):
            return None

        subagent_type = str(args.get("subagent_type", ""))
        description = str(args.get("description", ""))

        if self._enforce_allowlist and subagent_type not in self._allowed_subagents:
            logger.warning(
                "supervisor_policy_rejected",
                extra={
                    "event_fields": {
                        "supervisor_stage": SupervisorStage.DISPATCH.value,
                        "subagent_name": subagent_type,
                        "reason": "unregistered_workflow",
                    }
                },
            )
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

        # 1. 校验租户访问权限（RBAC）
        if workflow.allowed_tenants is not None:
            context = getattr(request.runtime, "context", None)
            tenant_id = getattr(context, "tenant_id", "default")
            if tenant_id not in workflow.allowed_tenants:
                logger.warning(
                    "supervisor_policy_rejected",
                    extra={
                        "event_fields": {
                            "supervisor_stage": SupervisorStage.DISPATCH.value,
                            "subagent_name": subagent_type,
                            "reason": "tenant_forbidden",
                        }
                    },
                )
                return ToolMessage(
                    content=(
                        f"[SupervisorPolicy] Tenant is not authorized to dispatch workflow "
                        f"'{subagent_type}'."
                    ),
                    tool_call_id=tool_call_id,
                    status="error",
                )

        messages = list((request.state or {}).get("messages") or [])
        dispatch_counts, completed_workflows, idempotent_cache = _inspect_history(messages)

        # 2. 校验前置工作流依赖关系（depends_on）
        missing_deps = [dep for dep in workflow.depends_on if dep not in completed_workflows]
        if missing_deps:
            logger.warning(
                "supervisor_policy_rejected",
                extra={
                    "event_fields": {
                        "supervisor_stage": SupervisorStage.DISPATCH.value,
                        "subagent_name": subagent_type,
                        "reason": "dependency_unmet",
                    }
                },
            )
            return ToolMessage(
                content=(
                    f"[SupervisorPolicy] Workflow '{subagent_type}' requires completed "
                    f"prerequisite workflows: {', '.join(missing_deps)}."
                ),
                tool_call_id=tool_call_id,
                status="error",
            )

        # 3. 校验幂等缓存（若已成功执行过完全一致的任务描述，直接复用验收结果，不计入额外重试消耗）
        if workflow.idempotent and tool_name == "task":
            idem_key = _idempotency_key(subagent_type, description)
            if idem_key in idempotent_cache:
                cached_output = idempotent_cache[idem_key]
                logger.info(
                    "supervisor_policy_idempotent_hit",
                    extra={
                        "event_fields": {
                            "supervisor_stage": SupervisorStage.DISPATCH.value,
                            "subagent_name": subagent_type,
                        }
                    },
                )
                return ToolMessage(
                    content=cached_output,
                    tool_call_id=tool_call_id,
                    artifact={"supervisor_policy": {"dispatched": False}},
                )

        # Reserve earlier calls from the same model batch, even when they run in parallel.
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

        # 4. 校验最大委派/重试次数上限（max_dispatches）
        if dispatch_counts[subagent_type] + reserved >= workflow.max_dispatches:
            logger.warning(
                "supervisor_policy_rejected",
                extra={
                    "event_fields": {
                        "supervisor_stage": SupervisorStage.DISPATCH.value,
                        "subagent_name": subagent_type,
                        "reason": "max_dispatches_exceeded",
                        "max_dispatches": workflow.max_dispatches,
                    }
                },
            )
            return ToolMessage(
                content=(
                    f"[SupervisorPolicy] Workflow '{subagent_type}' exceeded "
                    f"max_dispatches={workflow.max_dispatches}."
                ),
                tool_call_id=tool_call_id,
                status="error",
            )

        logger.debug(
            "supervisor_workflow_dispatched",
            extra={
                "event_fields": {
                    "supervisor_stage": SupervisorStage.DISPATCH.value,
                    "subagent_name": subagent_type,
                    "dispatch_attempt": dispatch_counts[subagent_type] + 1,
                }
            },
        )
        return None

    def _post_dispatch_validate(
        self,
        request: ToolCallRequest,
        result: ToolMessage | Command[Any],
    ) -> ToolMessage | Command[Any]:
        """在同步工作流返回后执行确定性输出模式验收（Validate 阶段）。"""
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

        schema: type[BaseModel] | None = workflow.result_schema
        if (
            schema is None
            and isinstance(workflow.output_schema, type)
            and issubclass(workflow.output_schema, BaseModel)
        ):
            schema = workflow.output_schema
        if schema is None:
            return result

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

        logger.debug(
            "supervisor_workflow_validated",
            extra={
                "event_fields": {
                    "supervisor_stage": SupervisorStage.VALIDATE.value,
                    "subagent_name": subagent_type,
                }
            },
        )
        return result

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        """同步拦截并监督工作流委派与结果验收。"""
        logger.debug("supervisor_wrapping_tool_call_sync")
        short_circuit = self._pre_dispatch_check(request)
        if short_circuit is not None:
            return short_circuit.model_copy(
                update={"artifact": {"supervisor_policy": {"dispatched": False}}}
            )
        result = handler(request)
        return self._mark_dispatch(request, self._post_dispatch_validate(request, result))

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        """异步拦截并监督工作流委派与结果验收。"""
        logger.debug("supervisor_wrapping_tool_call_async")
        short_circuit = self._pre_dispatch_check(request)
        if short_circuit is not None:
            return short_circuit.model_copy(
                update={"artifact": {"supervisor_policy": {"dispatched": False}}}
            )
        result = await handler(request)
        return self._mark_dispatch(request, self._post_dispatch_validate(request, result))

    def _mark_dispatch(
        self, request: ToolCallRequest, result: ToolMessage | Command[Any]
    ) -> ToolMessage | Command[Any]:
        """为通过委派的工作流调用结果标记 dispatched=True 元数据。

        :param request: 原始工具调用请求对象。
        :param result: 工具执行输出对象。
        :return: 注入元数据标记后的更新结果对象。
        """
        logger.debug("supervisor_marking_dispatch")
        if request.tool_call.get("name") not in _DISPATCH_TOOLS:
            return result
        msg = _extract_tool_message(result, request.tool_call.get("id"))
        if msg is None:
            return result
        artifact = dict(msg.artifact) if isinstance(msg.artifact, dict) else {}
        artifact["supervisor_policy"] = {"dispatched": True}
        marked = msg.model_copy(update={"artifact": artifact})
        if isinstance(result, Command) and isinstance(result.update, dict):
            return replace(
                result,
                update={
                    **result.update,
                    "messages": [
                        marked if m is msg else m for m in result.update.get("messages", [])
                    ],
                },
            )
        return marked


__all__ = [
    "SupervisorPolicyMiddleware",
    "SupervisorStage",
    "build_workflow_interrupt_configs",
]
