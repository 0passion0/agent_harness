"""将原生执行事件投影为可序列化的 UI 事件，不负责传输或管理 Agent 生命周期。

本模块提供 `AgentStreaming` 静态工具类：
1. 原生 LangGraph 事件流过滤投影（`astream_events(version="v2")`）；
2. 敏感数据与内部配置剥离：严格过滤原始图状态、工具内部参数与模型私有凭据，仅投影标准可显示的文本增量与状态事件；
3. 超时统一拦截：通过 `asyncio.timeout` 配合图元数据中的 `harness_run_timeout_seconds` 进行超时强杀；
4. 结构化事件输出：封装输出强类型的 `StreamEvent` 序列，支持单步序号自增（seq）与调用树祖先链（parent_ids）。
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, aclosing
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.runnables.config import merge_configs
from langchain_core.runnables.schema import CustomStreamEvent, StandardStreamEvent
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, Interrupt
from pydantic import JsonValue, TypeAdapter
from typing_extensions import TypeForm

from vertical_harness.core.context import RunContext
from vertical_harness.langchain.callbacks import WorkflowLoggingHandler
from vertical_harness.langchain.stream_event import StreamEvent, StreamEventType
from vertical_harness.log.logging import HarnessLogging

# 模块级日志记录器，记录事件流与执行调用周期
logger = logging.getLogger(__name__)

# JSON 序列化合法值严格校验适配器
_JSON: TypeAdapter[JsonValue] = TypeAdapter(TypeForm(JsonValue))


class AgentStreaming:
    """智能体执行事件流投影器，将 LangGraph 原始底层事件转换为前端规范的 StreamEvent 序列。

    纯静态/类方法集合，供 Desktop IPC 与 CLI 统一调用。
    """

    @staticmethod
    def _config(config: RunnableConfig) -> RunnableConfig:
        """为每次执行独立配置观测器，不修改调用方或图级共享 callbacks。

        :param config: 原生运行配置，保留已有 callback manager 和 run_id。
        :return: 含关联标识和独立计数器的新配置。
        """
        configured = merge_configs(config, {"callbacks": [WorkflowLoggingHandler()]})
        configured["run_id"] = config.get("run_id") or uuid4()
        return configured

    @staticmethod
    def _project(
        event: StandardStreamEvent | CustomStreamEvent,
        root_id: str,
        interrupts: tuple[Interrupt, ...],
    ) -> tuple[StreamEventType, dict[str, JsonValue]] | None:
        """只输出明确允许的字段；不转发原始 state、工具参数、工具结果或模型配置。

        将 LangChain 标准事件分类映射为前端 `StreamEventType`，过滤掉无文本内容的空增量。

        Args:
            event (StandardStreamEvent | CustomStreamEvent): LangGraph v2 事件流派发的单个底层原始事件。
            root_id (str): 整棵状态图执行树根调用的 operation_id 字符串。
            interrupts (tuple[Interrupt, ...]): 当前捕获到的人机交互中断列表。

        Returns:
            tuple[StreamEventType, dict[str, JsonValue]] | None: 映射成功的 `(event_type, data)` 二元组；
                若该事件无需向前端推送（如空增量或内部节点启动）则返回 None。

        Raises:
            TypeError: 当模型事件缺少 AIMessage 载荷时抛出。
            ValueError: 当工具命令 Command 未包含有效更新消息时抛出。
        """
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
        """计算智能体调用的超时秒数阈值。

        若显式提供了 `timeout_seconds` 则优先使用；否则从编译图元数据中的 `harness_run_timeout_seconds` 读取，默认 900 秒。

        Args:
            agent (CompiledStateGraph[Any, RunContext, Any, Any]): 已编译的状态图实例。
            timeout_seconds (float | None): 可选的调用方指定超时秒数。

        Returns:
            float: 最终确定的有限正浮点数超时时间（秒）。

        Raises:
            ValueError: 当计算出的超时不是大于 0 的有限数值时抛出。
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
        """在宿主控制的超时截止时限内异步调用原生编译状态图，支持人机审批恢复。

        Args:
            agent (CompiledStateGraph[Any, RunContext, Any, Any]): 已编译的 LangGraph 图实例。
            inputs (dict[str, Any] | Command[Any] | None): 初始消息输入字典，或用于恢复中断的 Command。
            config (RunnableConfig): 运行时配置字典，必须包含 `configurable: {"thread_id": ...}`。
            context (RunContext | None): 注入的可信运行上下文。
            durability (Literal["sync", "async", "exit"]): 检查点落盘同步级别，默认 `"sync"`。
            timeout_seconds (float | None): 单次调用的超时秒数。

        Returns:
            Any: 图执行完成后最终的状态字典。
        """
        logger.debug("ainvoke_agent_started")
        config = cls._config(config)
        with HarnessLogging.log_context(
            str(config["run_id"]), thread_id=config.get("configurable", {}).get("thread_id")
        ):
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
        """执行一次 Agent 并产生 UI 事件，支持 None / Command(resume=...) 输入。

        在配置的超时时间内拉起事件流（v2 协议），将底层的链、模型和工具事件实时转换为面向前端渲染的 `StreamEvent`。
        若执行发生未捕获异常，会在抛出前生成最终的 `run.failed` 事件。

        Args:
            agent (CompiledStateGraph[Any, RunContext, Any, Any]): 编译就绪的状态图实例。
            inputs (dict[str, Any] | Command[Any] | None): 输入字典或人机审批恢复命令。
            config (RunnableConfig): 包含 `thread_id` 的可配置项字典。
            context (RunContext | None): 可信身份与任务调度上下文。
            durability (Literal["sync", "async", "exit"]): 检查点同步策略。
            timeout_seconds (float | None): 任务总超时秒数。

        Yields:
            StreamEvent: 强类型可序列化的标准化流式事件实例。

        Raises:
            ValueError: 当 `config` 中缺少有效的 `thread_id` 时抛出。
            TypeError: 当事件流不是有效的异步生成器时抛出。
            Exception: 当图执行崩溃或超时时，派发 `run.failed` 后重新抛出异常。
        """
        logger.debug("astream_agent_started")
        thread_id = config.get("configurable", {}).get("thread_id")
        if not isinstance(thread_id, str) or not thread_id.strip():
            raise ValueError("Streaming requires a nonempty string thread_id")
        run_config = cls._config(config)
        run_id = run_config["run_id"]
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
            async with (
                AsyncExitStack() as stack,
                asyncio.timeout(cls._run_timeout(agent, timeout_seconds)),
                aclosing(source),
            ):
                stack.enter_context(HarnessLogging.log_context(str(run_id), thread_id=thread_id))
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
