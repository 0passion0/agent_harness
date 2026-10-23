"""通过原生 LangChain callbacks 观察完整执行树，不耦合智能体或插件实现。"""

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

logger = logging.getLogger(__name__)


class WorkflowLoggingHandler(BaseCallbackHandler):
    """记录链、模型、工具和子调用的阶段与耗时，不读取输入、输出或 token 内容。"""

    run_inline = True

    def __init__(self) -> None:
        """初始化工作流执行树日志观察处理器。"""
        super().__init__()
        self._started: dict[UUID, float] = {}
        self._lock = Lock()
        self._model_calls = 0
        self._usage_reported_calls = 0
        self._usage: UsageMetadata = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    @property
    def usage(self) -> dict[str, Any]:
        """汇总整个执行树的 Token 消耗量与完整性指标。"""
        with self._lock:
            return {
                **deepcopy(self._usage),
                "model_calls": self._model_calls,
                "usage_reported_calls": self._usage_reported_calls,
                "complete": self._model_calls == self._usage_reported_calls,
            }

    def _start(self, kind: str, serialized: dict[str, Any] | None, **kwargs: Any) -> None:
        """记录组件开始运行的起点时间戳与启动日志。"""
        run_id = kwargs["run_id"]
        with self._lock:
            self._started[run_id] = perf_counter()
            if kind == "model":
                self._model_calls += 1
        logger.debug(
            f"{kind}_started",
            extra={
                "event_fields": {
                    "operation_id": str(run_id),
                    "parent_operation_id": str(kwargs["parent_run_id"])
                    if kwargs.get("parent_run_id")
                    else None,
                    "component": kwargs.get("name") or (serialized or {}).get("name"),
                }
            },
        )

    def _finish(
        self,
        kind: str,
        *,
        error: BaseException | None = None,
        failed: bool = False,
        **kwargs: Any,
    ) -> None:
        """记录组件结束运行的耗时、状态分类及审计日志。"""
        run_id = kwargs["run_id"]
        with self._lock:
            started = self._started.pop(run_id, None)
        extra: dict[str, Any] = {
            "operation_id": str(run_id),
            "parent_operation_id": str(kwargs["parent_run_id"])
            if kwargs.get("parent_run_id")
            else None,
        }
        if started is not None:
            extra["duration_ms"] = round((perf_counter() - started) * 1000, 2)
        if isinstance(error, GraphBubbleUp):
            event, level = f"{kind}_suspended", logging.DEBUG
        elif isinstance(error, (CancelledError, KeyboardInterrupt)):
            event, level = f"{kind}_cancelled", logging.WARNING
        elif error is not None or failed:
            if error is not None:
                extra["error_type"] = type(error).__name__
            event, level = f"{kind}_failed", logging.ERROR
        else:
            event, level = f"{kind}_completed", logging.DEBUG
        logger.log(level, event, extra={"event_fields": extra})

    def on_chain_start(self, serialized: dict[str, Any] | None, inputs: Any, **kwargs: Any) -> None:
        """执行链或图节点开始运行时的回调入口。"""
        self._start("chain", serialized, **kwargs)

    def on_chain_end(self, outputs: Any, **kwargs: Any) -> None:
        """执行链或图节点成功运行结束时的回调入口。"""
        self._finish("chain", **kwargs)

    def on_chain_error(self, error: BaseException, **kwargs: Any) -> None:
        """执行链或图节点发生异常中断时的回调入口。"""
        self._finish("chain", error=error, **kwargs)

    def on_chat_model_start(self, serialized: dict[str, Any], messages: Any, **kwargs: Any) -> None:
        """聊天模型（Chat Model）开始发起调用时的回调入口。"""
        self._start("model", serialized, **kwargs)

    def on_llm_start(self, serialized: dict[str, Any], prompts: Any, **kwargs: Any) -> None:
        """大语言模型（LLM）开始发起调用时的回调入口。"""
        self._start("model", serialized, **kwargs)

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        """大语言模型调用成功完成并返回时的回调入口，累计 Token 消耗。"""
        usage = response.generations[0][0].message.usage_metadata
        if usage is not None:
            with self._lock:
                self._usage = add_usage(self._usage, usage)
                self._usage_reported_calls += 1
        self._finish("model", **kwargs)

    def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        """大语言模型调用发生异常或网络超时中断时的回调入口。"""
        self._finish("model", error=error, **kwargs)

    def on_tool_start(self, serialized: dict[str, Any], input_str: str, **kwargs: Any) -> None:
        """工具开始执行时的回调入口。"""
        self._start("tool", serialized, **kwargs)

    def on_tool_end(self, output: Any, **kwargs: Any) -> None:
        """工具执行结束时的回调入口，识别业务错误状态。"""
        self._finish("tool", failed=getattr(output, "status", None) == "error", **kwargs)

    def on_tool_error(self, error: BaseException, **kwargs: Any) -> None:
        """工具执行抛出未捕获异常时的回调入口。"""
        self._finish("tool", error=error, **kwargs)

    def on_retry(self, retry_state: Any, **kwargs: Any) -> None:
        """操作触发重试机制时的回调入口。"""
        logger.debug(
            "operation_retrying",
            extra={
                "event_fields": {
                    "operation_id": str(kwargs["run_id"]),
                    "attempt": retry_state.attempt_number,
                }
            },
        )
