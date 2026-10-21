"""OpenCode's bounded provider retry policy around native model calls."""

from __future__ import annotations

import asyncio
import math
import random
import re
import time
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse, hook_config
from langchain_core.exceptions import ModelError, ModelTimeoutError
from langchain_core.messages import AIMessage, HumanMessage, message_chunk_to_message
from openai import APIConnectionError, APIError, APIStatusError, APITimeoutError

from vertical_harness.middleware.model_output_guard import ModelOutputGuard


class OpenCodeModelRetryMiddleware(AgentMiddleware):
    """Retry model requests, leaving tool execution and graph cancellation untouched."""

    _errors = (ModelError, APIError, httpx.TransportError, httpx.HTTPStatusError)
    _timeouts = (ModelTimeoutError, APITimeoutError, httpx.TimeoutException)
    _quota_codes = (
        "insufficient_quota", "usage_not_included", "billing_error", "gousagelimiterror",
        "freeusagelimiterror", "creditlimitexceeded",
    )
    _quota_text = re.compile(
        r"insufficient[-_\s]?(?:quota|balance)|quota[-_\s]?exceeded|budget exceeded|"
        r"usage limit|limit exhausted|package has expired|plan does not yet include",
        re.IGNORECASE,
    )
    _continuation = (
        "The previous response was interrupted. Continue from where you left off "
        "without repeating completed content."
    )

    @staticmethod
    def _retry_state(request: ModelRequest[Any]) -> tuple[int, int]:
        for message in reversed(request.messages):
            if isinstance(message, HumanMessage) and message.additional_kwargs.get("opencode_synthetic"):
                continue
            if isinstance(message, AIMessage) and (retry := message.additional_kwargs.get("opencode_stream_retry")):
                return retry["attempt"], retry["timeouts"]
            break
        return 0, 0

    @staticmethod
    def _partial(output: ModelOutputGuard, error: Exception, attempt: int, timeouts: int) -> ModelResponse | None:
        chunk = output.chunk
        # Native tools execute only after a complete response; never execute unfinished calls.
        if chunk is None or not chunk.content or chunk.tool_call_chunks or chunk.tool_calls or chunk.invalid_tool_calls:
            return None
        if chunk.additional_kwargs.get("function_call") or chunk.additional_kwargs.get("tool_calls"):
            return None
        message = message_chunk_to_message(chunk)
        message.additional_kwargs = {
            **message.additional_kwargs,
            "opencode_stream_retry": {"attempt": attempt, "timeouts": timeouts},
        }
        message.response_metadata = {
            **message.response_metadata, "finish_reason": "error", "stream_error": str(error),
        }
        return ModelResponse(result=[message])

    @staticmethod
    def _headers(error: Exception) -> httpx.Headers:
        if isinstance(error, (APIStatusError, httpx.HTTPStatusError)):
            return error.response.headers
        return httpx.Headers()

    @classmethod
    def _retryable(cls, error: Exception) -> bool:
        override = cls._headers(error).get("x-should-retry")
        if override is not None:
            if override == "true":
                return True
            if override == "false":
                return False
        if isinstance(error, (APIStatusError, httpx.HTTPStatusError)):
            status = error.response.status_code
            if isinstance(error, APIStatusError) and (error.code or "").lower() in cls._quota_codes:
                return False
            if status == 429 and cls._quota_text.search(str(error)):
                return False
            return status in (408, 409, 429) or status >= 500
        if isinstance(error, ModelError):
            return error.is_retryable
        return isinstance(error, (APIConnectionError, httpx.TransportError))

    @classmethod
    def _delay(cls, error: Exception, attempt: int) -> float:
        delay = min(2 * 2**attempt, 10) * random.uniform(0.8, 1.2)
        headers = cls._headers(error)
        for header, divisor in (("retry-after-ms", 1000), ("retry-after", 1)):
            value = headers.get(header)
            if value is None:
                continue
            try:
                minimum = float(value) / divisor
            except ValueError:
                if header == "retry-after-ms":
                    continue
                try:
                    minimum = parsedate_to_datetime(value).timestamp() - time.time()
                except (ValueError, OverflowError):
                    continue
            if math.isfinite(minimum):
                delay = max(delay, min(max(0, minimum), 900))
                break
        return math.ceil(delay * 1000) / 1000

    def wrap_model_call(self, request: ModelRequest[Any], handler: Any) -> Any:
        start, timeouts = self._retry_state(request)
        with ModelOutputGuard() as output:
            for attempt in range(start, 11):
                output.chunk = None
                try:
                    return handler(request)
                except self._errors as error:
                    timeouts += isinstance(error, self._timeouts)
                    if attempt == 10 or timeouts > 3 or not self._retryable(error):
                        raise
                    partial = self._partial(output, error, attempt + 1, timeouts) if output.delivered else None
                    if output.delivered and partial is None:
                        raise
                    time.sleep(self._delay(error, attempt))
                    if partial is not None:
                        return partial

    async def awrap_model_call(self, request: ModelRequest[Any], handler: Any) -> Any:
        start, timeouts = self._retry_state(request)
        with ModelOutputGuard() as output:
            for attempt in range(start, 11):
                output.chunk = None
                try:
                    return await handler(request)
                except self._errors as error:
                    timeouts += isinstance(error, self._timeouts)
                    if attempt == 10 or timeouts > 3 or not self._retryable(error):
                        raise
                    partial = self._partial(output, error, attempt + 1, timeouts) if output.delivered else None
                    if output.delivered and partial is None:
                        raise
                    await asyncio.sleep(self._delay(error, attempt))
                    if partial is not None:
                        return partial

    @hook_config(can_jump_to=["model"])
    def after_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        if not state["messages"][-1].additional_kwargs.get("opencode_stream_retry"):
            return None
        return {
            "messages": [HumanMessage(
                content=self._continuation,
                additional_kwargs={"opencode_synthetic": "interrupted-stream"},
            )],
            "jump_to": "model",
        }

    @hook_config(can_jump_to=["model"])
    async def aafter_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        return self.after_model(state, runtime)
