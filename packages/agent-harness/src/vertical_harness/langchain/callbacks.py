"""通过原生 LangChain callbacks 观察完整执行树，不耦合智能体或插件实现。

本模块提供 `WorkflowLoggingHandler` 回调处理器，继承自 `BaseCallbackHandler`：
1. 阶段耗时度量：跟踪执行链（chain）、模型（model）、工具（tool）的启动与结束时间戳，自动计算毫秒耗时；
2. 异常与中断审计：细致区分图挂起（GraphBubbleUp）、用户主动取消（CancelledError/KeyboardInterrupt）、业务失败与未捕获异常；
3. Token 消耗统计：基于线程安全锁自动汇总多轮模型调用的输入/输出/总量 Token 消耗；
4. 隐私安全保障：仅记录操作元数据与耗时，严格不打印输入提示词、输出正文或敏感凭据。
"""

from __future__ import annotations

import logging
from asyncio import CancelledError
from copy import deepcopy
from threading import Lock
from time import perf_counter
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages.ai import UsageMetadata, add_usage
from langgraph.errors import GraphBubbleUp
from langgraph.types import Command

# 模块级日志记录器，记录执行树各生命周期事件
logger = logging.getLogger(__name__)


class WorkflowLoggingHandler(BaseCallbackHandler):
    """记录链、模型、工具和子调用的阶段与耗时，不读取输入、输出或 token 内容。

    Attributes:
        run_inline (bool): 标识该处理器是否在主执行流线程内同步触发（固定为 True）。
    """

    run_inline: bool = True

    def __init__(self) -> None:
        """初始化工作流执行树日志观察处理器。"""
        super().__init__()
        self._started: dict[UUID, float] = {}
        self._lock = Lock()
        self._model_calls = 0
        self._model_rounds = 0
        self._tool_calls = 0
        self._tool_errors = 0
        self._usage_reported_calls = 0
        self._usage: UsageMetadata = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    @property
    def usage(self) -> dict[str, Any]:
        """汇总整个执行树的 Token 消耗量与完整性指标。

        Returns:
            dict[str, Any]: 结构化 Token 消耗审计快照，包含如下键值：
                - `input_tokens` (int): 累计模型提示词消耗的 Token 总量；
                - `output_tokens` (int): 累计模型生成内容消耗的 Token 总量；
                - `total_tokens` (int): 累计消耗的总 Token 数；
                - `model_calls` (int): 发起模型调用的总次数；
                - `usage_reported_calls` (int): 成功返回 Token 元信息的调用次数；
                - `complete` (bool): 是否每一次模型调用均成功报告了 Token 用量。
        """
        with self._lock:
            return {
                **deepcopy(self._usage),
                "model_calls": self._model_calls,
                "model_rounds": self._model_rounds,
                "tool_calls": self._tool_calls,
                "tool_errors": self._tool_errors,
                "tool_error_rate": self._tool_errors / self._tool_calls if self._tool_calls else 0,
                "usage_reported_calls": self._usage_reported_calls,
                "complete": self._model_calls == self._usage_reported_calls,
            }

    def _start(self, kind: str, serialized: dict[str, Any] | None, **kwargs: Any) -> None:
        """记录组件开始运行的起点时间戳与启动日志。

        Args:
            kind (str): 组件类别名称（如 `"chain"`, `"model"`, `"tool"`）。
            serialized (dict[str, Any] | None): 组件的序列化元信息字典。
            **kwargs (Any): 包含 `run_id`, `parent_run_id`, `name` 等标识符的元数据字典。
        """
        run_id = kwargs["run_id"]
        component = kwargs.get("name") or (serialized or {}).get("name")
        with self._lock:
            self._started[run_id] = perf_counter()
            if kind == "model":
                self._model_calls += 1
            elif kind == "tool":
                self._tool_calls += 1
            elif kind == "chain" and component == "model":
                self._model_rounds += 1
        logger.debug(
            f"{kind}_started",
            extra={
                "event_fields": {
                    "operation_id": str(run_id),
                    "parent_operation_id": str(kwargs["parent_run_id"])
                    if kwargs.get("parent_run_id")
                    else None,
                    "component": component,
                }
            },
        )

    def _finish(
        self,
        kind: str,
        *,
        error: BaseException | None = None,
        failed: bool = False,
        suspended: bool = False,
        **kwargs: Any,
    ) -> None:
        """记录组件结束运行的耗时、状态分类及审计日志。

        计算执行耗时，并根据异常类型区分为正常完成（completed）、暂停挂起（suspended）、
        主动取消（cancelled）或失败崩溃（failed）。

        Args:
            kind (str): 组件类别标识。
            error (BaseException | None): 执行抛出的异常对象（若有）。
            failed (bool): 标识工具或步骤是否返回了业务失败状态。
            **kwargs (Any): 包含 `run_id`, `parent_run_id` 的调用参数。
        """
        run_id = kwargs["run_id"]
        with self._lock:
            started = self._started.pop(run_id, None)
            if (
                kind == "tool"
                and (failed or error is not None)
                and not isinstance(error, (GraphBubbleUp, CancelledError, KeyboardInterrupt))
            ):
                self._tool_errors += 1
        extra: dict[str, Any] = {
            "operation_id": str(run_id),
            "parent_operation_id": str(kwargs["parent_run_id"])
            if kwargs.get("parent_run_id")
            else None,
        }
        if started is not None:
            extra["duration_ms"] = round((perf_counter() - started) * 1000, 2)
        if suspended or isinstance(error, GraphBubbleUp):
            event, level = f"{kind}_suspended", logging.DEBUG
        elif isinstance(error, (CancelledError, KeyboardInterrupt)):
            event, level = f"{kind}_cancelled", logging.WARNING
        elif error is not None or failed:
            if error is not None:
                extra["error_type"] = type(error).__name__
            event, level = f"{kind}_failed", logging.ERROR
        else:
            event, level = f"{kind}_completed", logging.INFO if kind != "chain" else logging.DEBUG
        if kind == "chain" and kwargs.get("parent_run_id") is None:
            extra.update(self.usage)
            if error is None:
                level = max(level, logging.INFO)
        logger.log(level, event, extra={"event_fields": extra})

    def on_chain_start(self, serialized: dict[str, Any] | None, inputs: Any, **kwargs: Any) -> None:
        """执行链或图节点开始运行时的回调入口。

        Args:
            serialized (dict[str, Any] | None): 链配置元数据。
            inputs (Any): 链输入参数（忽略）。
            **kwargs (Any): 运行时上下文字典。
        """
        self._start("chain", serialized, **kwargs)

    def on_chain_end(self, outputs: Any, **kwargs: Any) -> None:
        """执行链或图节点成功运行结束时的回调入口。

        Args:
            outputs (Any): 链输出结果。
            **kwargs (Any): 运行时元数据。
        """
        self._finish(
            "chain",
            suspended=isinstance(outputs, dict) and bool(outputs.get("__interrupt__")),
            **kwargs,
        )

    def on_chain_error(self, error: BaseException, **kwargs: Any) -> None:
        """执行链或图节点发生异常中断时的回调入口。

        Args:
            error (BaseException): 捕获到的异常对象。
            **kwargs (Any): 运行时元数据。
        """
        self._finish("chain", error=error, **kwargs)

    def on_chat_model_start(self, serialized: dict[str, Any], messages: Any, **kwargs: Any) -> None:
        """聊天模型（Chat Model）开始发起调用时的回调入口。

        Args:
            serialized (dict[str, Any]): 模型配置信息。
            messages (Any): 发送给模型的输入消息列表（忽略以防泄露）。
            **kwargs (Any): 运行时元数据。
        """
        self._start("model", serialized, **kwargs)

    def on_llm_start(self, serialized: dict[str, Any], prompts: Any, **kwargs: Any) -> None:
        """大语言模型（LLM）开始发起调用时的回调入口。

        Args:
            serialized (dict[str, Any]): 模型配置信息。
            prompts (Any): 输入提示词。
            **kwargs (Any): 运行时元数据。
        """
        self._start("model", serialized, **kwargs)

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        """大语言模型调用成功完成并返回时的回调入口，累计 Token 消耗。

        从返回消息的 `usage_metadata` 中提取输入/输出/总量 Token，并通过锁原子性累加。

        Args:
            response (Any): 包含推理结果与用量元信息的模型响应实体。
            **kwargs (Any): 运行时元数据。
        """
        usage = response.generations[0][0].message.usage_metadata
        if usage is not None:
            with self._lock:
                self._usage = add_usage(self._usage, usage)
                self._usage_reported_calls += 1
        self._finish("model", **kwargs)

    def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        """大语言模型调用发生异常或网络超时中断时的回调入口。

        Args:
            error (BaseException): 捕获到的异常对象。
            **kwargs (Any): 运行时元数据。
        """
        self._finish("model", error=error, **kwargs)

    def on_tool_start(self, serialized: dict[str, Any], input_str: str, **kwargs: Any) -> None:
        """工具开始执行时的回调入口。

        Args:
            serialized (dict[str, Any]): 工具配置元信息。
            input_str (str): 工具调用入参字符串（忽略）。
            **kwargs (Any): 运行时元数据。
        """
        self._start("tool", serialized, **kwargs)

    def on_tool_end(self, output: Any, **kwargs: Any) -> None:
        """工具执行结束时的回调入口，识别业务错误状态。

        Args:
            output (Any): 工具返回结果对象。
            **kwargs (Any): 运行时元数据。
        """
        messages = (
            output.update.get("messages", [])
            if isinstance(output, Command) and isinstance(output.update, dict)
            else [output]
        )
        self._finish(
            "tool", failed=any(getattr(m, "status", None) == "error" for m in messages), **kwargs
        )

    def on_tool_error(self, error: BaseException, **kwargs: Any) -> None:
        """工具执行抛出未捕获异常时的回调入口。

        Args:
            error (BaseException): 工具执行抛出的异常。
            **kwargs (Any): 运行时元数据。
        """
        self._finish("tool", error=error, **kwargs)

    def on_retry(self, retry_state: Any, **kwargs: Any) -> None:
        """操作触发重试机制时的回调入口。

        Args:
            retry_state (Any): 重试状态实体，包含当前重试轮次 `attempt_number`。
            **kwargs (Any): 运行时元数据。
        """
        logger.debug(
            "operation_retrying",
            extra={
                "event_fields": {
                    "operation_id": str(kwargs["run_id"]),
                    "attempt": retry_state.attempt_number,
                }
            },
        )
