"""Supervisor 确定性监督策略中间件：管控工作流白名单、依赖顺序、重试上限、幂等性与结果验收。"""

from __future__ import annotations

import hashlib
import logging
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import replace
from enum import StrEnum
from typing import Any

from langchain.agents.middleware import AgentMiddleware, InterruptOnConfig
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
    payload = f"{subagent_type}::{description.strip()}".encode()
    return hashlib.sha256(payload).hexdigest()


def _extract_tool_message(
    result: ToolMessage | Command[Any],
    tool_call_id: str | None,
) -> ToolMessage | None:
    """从工具执行返回值（ToolMessage 或 LangGraph Command）中提取对应的 ToolMessage。"""
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
        dispatch_counts[subagent_type] += 1
        content = msg.content if isinstance(msg.content, str) else str(msg.content)
        is_error = getattr(msg, "status", None) == "error" or content.startswith(
            ("We cannot invoke subagent", "Failed to launch async subagent", "[SupervisorPolicy]")
        )
        if not is_error:
            completed_workflows.add(subagent_type)
            if tool_name == "task":
                idempotent_cache[_idempotency_key(subagent_type, description)] = content

    return dispatch_counts, completed_workflows, idempotent_cache


def _normalize_workflow_map(
    workflows: Mapping[str, WorkflowContribution] | Sequence[WorkflowContribution],
) -> dict[str, WorkflowContribution]:
    """将工作流字典或序列统一规范化为 {name: WorkflowContribution} 字典。"""
    if isinstance(workflows, Mapping):
        return dict(workflows)
    return {wf.name: wf for wf in workflows}


def _make_workflow_interrupt_predicate(allowed: frozenset[str]) -> Any:
    """构造同时兼容 ToolInterruptPredicate(tool_call, state, runtime) 与 ToolCallRequest 的审批谓词。"""

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

    利用 LangChain `InterruptOnConfig(when=...)` 谓词，仅在主智能体委派需要审批的工作流时挂起，
    纯只读工作流自动放行。

    :param workflows: 已注册的工作流贡献字典或序列。
    :param preferred_mode: 宿主偏好的工作流运行模式。
    :return: 工具名称（'task' 或 'start_async_task'）到 InterruptOnConfig 的映射。
    """
    workflow_map = _normalize_workflow_map(workflows)
    approval_names = frozenset(
        wf.name for wf in workflow_map.values() if wf.requires_approval
    )
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

    def _pre_dispatch_check(
        self,
        request: ToolCallRequest,
    ) -> ToolMessage | None:
        """在委派工作流前执行确定性策略检查；若拒绝或命中幂等缓存则直接返回 ToolMessage。"""
        tool_name = request.tool_call.get("name", "")
        if tool_name in _MONITOR_TOOLS:
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
            if cached_output := idempotent_cache.get(idem_key):
                logger.info(
                    "supervisor_policy_idempotent_hit",
                    extra={
                        "event_fields": {
                            "supervisor_stage": SupervisorStage.DISPATCH.value,
                            "subagent_name": subagent_type,
                        }
                    },
                )
                return ToolMessage(content=cached_output, tool_call_id=tool_call_id)

        # 4. 校验最大委派/重试次数上限（max_dispatches）
        if dispatch_counts[subagent_type] >= workflow.max_dispatches:
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
        if tool_msg is None:
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
                return replace(result, update={**result.update, "messages": [error_msg]})
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
        short_circuit = self._pre_dispatch_check(request)
        if short_circuit is not None:
            return short_circuit
        result = handler(request)
        return self._post_dispatch_validate(request, result)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        """异步拦截并监督工作流委派与结果验收。"""
        short_circuit = self._pre_dispatch_check(request)
        if short_circuit is not None:
            return short_circuit
        result = await handler(request)
        return self._post_dispatch_validate(request, result)


__all__ = [
    "SupervisorPolicyMiddleware",
    "SupervisorStage",
    "build_workflow_interrupt_configs",
]
