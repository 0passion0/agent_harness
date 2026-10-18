"""将原生执行事件投影为可序列化的 UI 事件，不负责传输或管理 Agent 生命周期。"""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import Any, Literal
from uuid import uuid4

from langchain_core.messages import BaseMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, Interrupt
from pydantic import BaseModel, ConfigDict, JsonValue, TypeAdapter

from vertical_harness.plugins.contracts import RunContext

logger = logging.getLogger(__name__)
_JSON = TypeAdapter(JsonValue)


class StreamEvent(BaseModel):
    """一次执行内 seq 递增；operation_id / parent_ids 标识并行、嵌套调用。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: Literal[
        "run.started",
        "run.completed",
        "run.interrupted",
        "run.failed",
        "chain.started",
        "chain.completed",
        "subagent.started",
        "subagent.completed",
        "model.started",
        "model.delta",
        "model.completed",
        "tool.started",
        "tool.completed",
        "tool.failed",
        "custom.progress",
    ]
    run_id: str
    thread_id: str
    seq: int
    operation_id: str
    parent_ids: list[str]
    name: str
    agent_name: str | None = None
    node: str | None = None
    data: dict[str, JsonValue]


def _project(
    event: dict[str, Any],
    root_id: str,
    interrupts: tuple[Interrupt, ...],
) -> tuple[str, dict[str, JsonValue]] | None:
    """只输出明确允许的字段；不转发原始 state、工具参数、工具结果或模型配置。"""
    kind = event["event"]
    data = event["data"]
    if kind == "on_custom_event":
        return "custom.progress", {"value": _JSON.validate_python(data)}
    if kind.startswith("on_chat_model_"):
        phase = kind.removeprefix("on_chat_model_")
        if phase == "start":
            return "model.started", {}
        message = data.get("chunk" if phase == "stream" else "output")
        if isinstance(message, BaseMessage) and phase in {"stream", "end"}:
            if phase == "stream" and not message.text:
                return None
            projected = {"text": message.text, "message_id": message.id}
            if phase == "end" and getattr(message, "usage_metadata", None) is not None:
                projected["usage"] = _JSON.validate_python(message.usage_metadata)
            return (
                "model.delta" if phase == "stream" else "model.completed",
                projected,
            )
    if kind == "on_tool_start":
        # 委派名称可用于显示任务卡片，任务描述和其他参数不自动公开。
        inputs = data.get("input")
        if event["name"] in {"task", "start_async_task"} and isinstance(inputs, dict):
            name = inputs.get("subagent_type")
            if isinstance(name, str):
                return "tool.started", {"subagent_type": name}
        return "tool.started", {}
    if kind == "on_tool_error":
        return "tool.failed", {"error_type": type(data.get("error")).__name__}
    if kind == "on_tool_end":
        failed = getattr(data.get("output"), "status", None) == "error"
        return "tool.failed" if failed else "tool.completed", {}
    if kind in {"on_chain_start", "on_chain_end"}:
        if event["run_id"] == root_id:
            if kind == "on_chain_start":
                return "run.started", {}
            output = data.get("output")
            output = output if isinstance(output, dict) else {}
            interrupts = interrupts or output.get("__interrupt__", ())
            if interrupts:
                return "run.interrupted", {
                    "interrupts": [
                        {"id": item.id, "value": _JSON.validate_python(item.value)}
                        for item in interrupts
                    ]
                }
            messages = output.get("messages", [])
            text = messages[-1].text if messages and isinstance(messages[-1], BaseMessage) else ""
            return "run.completed", {"output": text}
        agent_name = event.get("metadata", {}).get("lc_agent_name")
        prefix = "subagent" if agent_name and event["name"] == agent_name else "chain"
        phase = "started" if kind == "on_chain_start" else "completed"
        return f"{prefix}.{phase}", {}
    return None


def _run_timeout(agent: CompiledStateGraph, timeout_seconds: float | None) -> float:
    """计算智能体调用的超时秒数阈值。

    :param agent: 已编译的状态图实例。
    :param timeout_seconds: 显式传入的可选超时时间。
    :return: 经过有效性校验的有限正浮点数超时时间。
    :raises ValueError: 当超时数值非正或非有限数时抛出。
    """
    logger.debug("calculating_run_timeout")
    timeout = (
        timeout_seconds
        if timeout_seconds is not None
        else (agent.config or {}).get("metadata", {}).get("harness_run_timeout_seconds", 900)
    )
    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout_seconds must be positive")
    return timeout


async def ainvoke_agent(
    agent: CompiledStateGraph,
    inputs: dict[str, Any] | Command[Any] | None,
    *,
    config: RunnableConfig,
    context: RunContext | None = None,
    durability: Literal["sync", "async", "exit"] = "sync",
    timeout_seconds: float | None = None,
) -> Any:
    """在宿主控制的超时截止时限内异步调用原生编译状态图，支持人机审批恢复。

    :param agent: 原生编译状态图对象。
    :param inputs: 提示词消息字典、恢复执行 Command 或 None。
    :param config: 调用方传入的检查点与回调配置。
    :param context: 可信身份与租户上下文。
    :param durability: 检查点持久化同步级别。
    :param timeout_seconds: 覆盖默认调用超时的秒数。
    :return: 智能体图执行的最终状态输出。
    """
    logger.debug("ainvoke_agent_started")
    async with asyncio.timeout(_run_timeout(agent, timeout_seconds)):
        result = await agent.ainvoke(
            inputs,
            config=config,
            context=context,
            durability=durability if agent.checkpointer else None,
        )
    logger.debug("ainvoke_agent_completed")
    return result


async def astream_agent(
    agent: CompiledStateGraph,
    inputs: dict[str, Any] | Command[Any] | None,
    *,
    config: RunnableConfig,
    context: RunContext | None = None,
    durability: Literal["sync", "async", "exit"] = "sync",
    timeout_seconds: float | None = None,
) -> AsyncIterator[StreamEvent]:
    """执行一次 Agent 并产生 UI 事件，支持 None / Command(resume=...) 输入。

    config 必须提供 thread_id；保留调用方 callbacks、checkpoint 等配置。
    失败先发送 run.failed 再重新抛出异常。取消原样传播；消费者提前停止时须
    使用 aclosing，确保底层执行被取消。Agent/MCP/store 由外层 aopen_agent 管理。

    :param agent: 原生编译状态图对象。
    :param inputs: 消息载荷或中断恢复指令。
    :param config: 必须包含 thread_id 的 RunnableConfig 字典。
    :param context: 可信运行时身份。
    :param durability: 检查点持久化同步级别。
    :param timeout_seconds: 执行超时秒数。
    :return: 投影后的 StreamEvent 异步事件流迭代器。
    :raises ValueError: 当缺少 thread_id 时抛出。
    """
    logger.debug("astream_agent_started")
    thread_id = config.get("configurable", {}).get("thread_id")
    if not isinstance(thread_id, str) or not thread_id.strip():
        raise ValueError("Streaming requires a nonempty string thread_id")
    run_id = config.get("run_id") or uuid4()
    run_config = {**config, "run_id": run_id}
    root_id = str(run_id)
    seq = 0
    interrupts: tuple[Interrupt, ...] = ()
    source = agent.astream_events(
        inputs,
        config=run_config,
        version="v2",
        context=context,
        durability=durability if agent.checkpointer else None,
    )
    try:
        async with asyncio.timeout(_run_timeout(agent, timeout_seconds)), aclosing(source):
            async for raw in source:
                if seq == 0:
                    root_id = raw["run_id"]
                if raw["run_id"] == root_id and raw["event"] == "on_chain_stream":
                    chunk = raw["data"].get("chunk")
                    if isinstance(chunk, dict) and "__interrupt__" in chunk:
                        # LangGraph 的 end 输出可能省略 interrupt，只在根 stream chunk 中携带。
                        interrupts = tuple(chunk["__interrupt__"])
                projected = _project(raw, root_id, interrupts)
                if projected is None:
                    continue
                event_type, data = projected
                metadata = raw.get("metadata", {})
                seq += 1
                yield StreamEvent(
                    type=event_type,
                    run_id=str(run_id),
                    thread_id=thread_id,
                    seq=seq,
                    operation_id=raw["run_id"],
                    parent_ids=raw.get("parent_ids", []),
                    name=raw["name"],
                    agent_name=metadata.get("lc_agent_name"),
                    node=metadata.get("langgraph_node"),
                    data=data,
                )
        logger.debug("astream_agent_completed", extra={"event_fields": {"events_count": seq}})
    except Exception as exc:
        yield StreamEvent(
            type="run.failed",
            run_id=str(run_id),
            thread_id=thread_id,
            seq=seq + 1,
            operation_id=root_id,
            parent_ids=[],
            name=agent.name or "agent",
            data={"error_type": type(exc).__name__},
        )
        raise


__all__ = ["StreamEvent", "ainvoke_agent", "astream_agent"]
