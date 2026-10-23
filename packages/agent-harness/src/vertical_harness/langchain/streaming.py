"""将原生执行事件投影为可序列化的 UI 事件，不负责传输或管理 Agent 生命周期。"""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import AsyncGenerator
from contextlib import aclosing
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.runnables.schema import CustomStreamEvent, StandardStreamEvent
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, Interrupt
from pydantic import JsonValue, TypeAdapter
from typing_extensions import TypeForm

from vertical_harness.core.context import RunContext
from vertical_harness.langchain.stream_event import StreamEvent, StreamEventType

logger = logging.getLogger(__name__)
_JSON: TypeAdapter[JsonValue] = TypeAdapter(TypeForm(JsonValue))


class AgentStreaming:
    @staticmethod
    def _project(
        event: StandardStreamEvent | CustomStreamEvent,
        root_id: str,
        interrupts: tuple[Interrupt, ...],
    ) -> tuple[StreamEventType, dict[str, JsonValue]] | None:
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
            if phase in {"stream", "end"}:
                if not isinstance(message, AIMessage):
                    raise TypeError("Model event must include an AIMessage")
                if phase == "stream" and not message.text:
                    return None
                projected: dict[str, JsonValue] = {"text": message.text, "message_id": message.id}
                if phase == "end":
                    metadata = message.response_metadata
                    model = (
                        metadata.get("model_name")
                        or metadata.get("model")
                        or event.get("metadata", {}).get("ls_model_name")
                    )
                    if model:
                        projected["model"] = model
                if phase == "end" and message.usage_metadata is not None:
                    projected["usage"] = _JSON.validate_python(message.usage_metadata)
                return (
                    "model.delta" if phase == "stream" else "model.completed",
                    projected,
                )
        if kind == "on_tool_start":
            return "tool.started", {}
        if kind == "on_tool_error":
            return "tool.failed", {"error_type": type(data.get("error")).__name__}
        if kind == "on_tool_end":
            output = data["output"]
            if isinstance(output, Command):
                if not isinstance(output.update, dict) or not output.update.get("messages"):
                    raise ValueError("Tool command must include messages")
                message = output.update["messages"][-1]
            else:
                message = output
            failed = getattr(message, "status", None) == "error"
            return "tool.failed" if failed else "tool.completed", {}
        if kind in {"on_chain_start", "on_chain_end", "on_chain_error"}:
            if event["run_id"] == root_id:
                if kind == "on_chain_start":
                    return "run.started", {}
                if kind == "on_chain_error":
                    return None
                output = data.get("output")
                output = output or {}
                interrupts = interrupts or output.get("__interrupt__", ())
                if interrupts:
                    return "run.interrupted", {
                        "interrupts": [
                            {"id": item.id, "value": _JSON.validate_python(item.value)}
                            for item in interrupts
                        ]
                    }
                messages = output.get("messages", [])
                text = messages[-1].text if messages else ""
                return "run.completed", {"output": text}
            chain_events: dict[str, StreamEventType] = {
                "on_chain_start": "chain.started",
                "on_chain_end": "chain.completed",
                "on_chain_error": "chain.failed",
            }
            return chain_events[kind], {}

        return None

    @staticmethod
    def _run_timeout(
        agent: CompiledStateGraph[Any, RunContext, Any, Any], timeout_seconds: float | None
    ) -> float:
        """计算智能体调用的超时秒数阈值。"""
        logger.debug("calculating_run_timeout")
        timeout = (
            timeout_seconds
            if timeout_seconds is not None
            else (agent.config or {}).get("metadata", {}).get("harness_run_timeout_seconds", 900)
        )
        if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout_seconds must be positive")
        return timeout

    @classmethod
    async def ainvoke_agent(
        cls,
        agent: CompiledStateGraph[Any, RunContext, Any, Any],
        inputs: dict[str, Any] | Command[Any] | None,
        *,
        config: RunnableConfig,
        context: RunContext | None = None,
        durability: Literal["sync", "async", "exit"] = "sync",
        timeout_seconds: float | None = None,
    ) -> Any:
        """在宿主控制的超时截止时限内异步调用原生编译状态图，支持人机审批恢复。"""
        logger.debug("ainvoke_agent_started")
        async with asyncio.timeout(cls._run_timeout(agent, timeout_seconds)):
            result = await agent.ainvoke(
                inputs,
                config=config,
                context=context,
                durability=durability if agent.checkpointer else None,
            )
        logger.debug("ainvoke_agent_completed")
        return result

    @classmethod
    async def astream_agent(
        cls,
        agent: CompiledStateGraph[Any, RunContext, Any, Any],
        inputs: dict[str, Any] | Command[Any] | None,
        *,
        config: RunnableConfig,
        context: RunContext | None = None,
        durability: Literal["sync", "async", "exit"] = "sync",
        timeout_seconds: float | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """执行一次 Agent 并产生 UI 事件，支持 None / Command(resume=...) 输入。"""
        logger.debug("astream_agent_started")
        thread_id = config.get("configurable", {}).get("thread_id")
        if not isinstance(thread_id, str) or not thread_id.strip():
            raise ValueError("Streaming requires a nonempty string thread_id")
        run_id = config.get("run_id") or uuid4()
        run_config: RunnableConfig = {**config, "run_id": run_id}
        root_id = str(run_id)
        seq = 0
        interrupts: tuple[Interrupt, ...] = ()
        model_started: dict[str, str] = {}
        source = agent.astream_events(
            inputs,
            config=run_config,
            version="v2",
            context=context,
            durability=durability if agent.checkpointer else None,
        )
        if not isinstance(source, AsyncGenerator):
            raise TypeError("The v2 event stream must be a closeable async generator")
        try:
            async with asyncio.timeout(cls._run_timeout(agent, timeout_seconds)), aclosing(source):
                async for raw in source:
                    if seq == 0:
                        root_id = raw["run_id"]
                    if raw["run_id"] == root_id and raw["event"] == "on_chain_stream":
                        chunk = raw["data"].get("chunk")
                        if isinstance(chunk, dict) and "__interrupt__" in chunk:
                            interrupts = tuple(chunk["__interrupt__"])
                    projected = cls._project(raw, root_id, interrupts)
                    if projected is None:
                        continue
                    event_type, data = projected
                    metadata = raw.get("metadata", {})
                    if event_type in {"model.started", "model.completed"}:
                        model = (
                            metadata.get("ls_model_name")
                            or metadata.get("model_name")
                            or metadata.get("model")
                        )
                        if model is not None:
                            data["model"] = str(model)
                        if event_type == "model.started":
                            requested_at = datetime.now(UTC).isoformat()
                            model_started[raw["run_id"]] = requested_at
                        else:
                            data["requested_at"] = model_started.pop(
                                raw["run_id"],
                                datetime.now(UTC).isoformat(),
                            )
                    seq += 1
                    yield StreamEvent(
                        type=event_type,
                        run_id=str(run_id),
                        thread_id=thread_id,
                        seq=seq,
                        operation_id=raw["run_id"],
                        parent_ids=list(raw.get("parent_ids", [])),
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
