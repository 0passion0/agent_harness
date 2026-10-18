"""通过原生 LangChain callbacks 观察完整执行树，不耦合智能体或插件实现。"""

from __future__ import annotations

import logging
from asyncio import CancelledError
from threading import Lock
from time import perf_counter
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langgraph.errors import GraphBubbleUp

logger = logging.getLogger(__name__)


class WorkflowLoggingHandler(BaseCallbackHandler):
    """记录链、模型、工具和子调用的阶段与耗时，不读取输入、输出或 token 内容。

    每次运行创建独立实例，通过 RunnableConfig.callbacks 注入；同步和异步通用。
    """

    run_inline = True

    def __init__(self) -> None:
        self._started: dict[UUID, float] = {}
        self._lock = Lock()

    def _start(self, kind: str, serialized: dict[str, Any] | None, **kwargs: Any) -> None:
        run_id = kwargs["run_id"]
        with self._lock:
            self._started[run_id] = perf_counter()
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
        self._start("chain", serialized, **kwargs)

    def on_chain_end(self, outputs: Any, **kwargs: Any) -> None:
        self._finish("chain", **kwargs)

    def on_chain_error(self, error: BaseException, **kwargs: Any) -> None:
        self._finish("chain", error=error, **kwargs)

    def on_chat_model_start(self, serialized: dict[str, Any], messages: Any, **kwargs: Any) -> None:
        self._start("model", serialized, **kwargs)

    def on_llm_start(self, serialized: dict[str, Any], prompts: Any, **kwargs: Any) -> None:
        self._start("model", serialized, **kwargs)

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        self._finish("model", **kwargs)

    def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        self._finish("model", error=error, **kwargs)

    def on_tool_start(self, serialized: dict[str, Any], input_str: str, **kwargs: Any) -> None:
        self._start("tool", serialized, **kwargs)

    def on_tool_end(self, output: Any, **kwargs: Any) -> None:
        self._finish("tool", failed=getattr(output, "status", None) == "error", **kwargs)

    def on_tool_error(self, error: BaseException, **kwargs: Any) -> None:
        self._finish("tool", error=error, **kwargs)

    def on_retry(self, retry_state: Any, **kwargs: Any) -> None:
        logger.debug(
            "operation_retrying",
            extra={
                "event_fields": {
                    "operation_id": str(kwargs["run_id"]),
                    "attempt": retry_state.attempt_number,
                }
            },
        )


__all__ = ["WorkflowLoggingHandler"]
