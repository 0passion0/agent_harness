"""大模型调用指数退避重试与中断流式恢复中间件模块。"""

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


class ModelRetryMiddleware(AgentMiddleware):
    """基于状态码与响应头的智能体模型层指数退避重试中间件，支持流式中断接续。

    :author: tongliga
    :date: 2026-10-21
    """

    _errors = (ModelError, APIError, httpx.TransportError, httpx.HTTPStatusError)
    _timeouts = (ModelTimeoutError, APITimeoutError, httpx.TimeoutException)
    _quota_codes = (
        "insufficient_quota",
        "usage_not_included",
        "billing_error",
        "gousagelimiterror",
        "freeusagelimiterror",
        "creditlimitexceeded",
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
        """从历史消息列表中提取上一次重试尝试次数与超时计数。

        :param request: 模型请求对象。
        :type request: langchain.agents.middleware.ModelRequest[typing.Any]
        :return: (已重试次数, 超时次数) 元组。
        :rtype: tuple[int, int]
        :author: tongliga
        :date: 2026-10-21
        """
        for message in reversed(request.messages):
            if isinstance(message, HumanMessage) and (
                message.additional_kwargs.get("harness_synthetic")
                or message.additional_kwargs.get("opencode_synthetic")
            ):
                continue
            if isinstance(message, AIMessage) and (
                retry := (
                    message.additional_kwargs.get("harness_stream_retry")
                    or message.additional_kwargs.get("opencode_stream_retry")
                )
            ):
                return retry["attempt"], retry["timeouts"]
            break
        return 0, 0

    @staticmethod
    def _partial(
        output: ModelOutputGuard, error: Exception, attempt: int, timeouts: int
    ) -> ModelResponse | None:
        """当流式传输中断时，将已交付的部分文本块封装为标记错误的 AIMessage。

        :param output: 模型输出守卫对象。
        :type output: vertical_harness.middleware.model_output_guard.ModelOutputGuard
        :param error: 触发中断的异常实例。
        :type error: Exception
        :param attempt: 当前重试次数。
        :type attempt: int
        :param timeouts: 当前累计超时次数。
        :type timeouts: int
        :return: 包含截断消息的 ModelResponse 或 None（未输出有效纯文本）。
        :rtype: langchain.agents.middleware.ModelResponse | None
        :author: tongliga
        :date: 2026-10-21
        """
        chunk = output.chunk
        if (
            chunk is None
            or not chunk.content
            or chunk.tool_call_chunks
            or chunk.tool_calls
            or chunk.invalid_tool_calls
        ):
            return None
        if chunk.additional_kwargs.get("function_call") or chunk.additional_kwargs.get(
            "tool_calls"
        ):
            return None
        message = message_chunk_to_message(chunk)
        message.additional_kwargs = {
            **message.additional_kwargs,
            "harness_stream_retry": {"attempt": attempt, "timeouts": timeouts},
            "opencode_stream_retry": {"attempt": attempt, "timeouts": timeouts},
        }
        message.response_metadata = {
            **message.response_metadata,
            "finish_reason": "error",
            "stream_error": str(error),
        }
        return ModelResponse(result=[message])

    @staticmethod
    def _headers(error: Exception) -> httpx.Headers:
        """从 HTTP 异常对象中提取响应头。

        :param error: 待检测异常。
        :type error: Exception
        :return: httpx.Headers 响应头对象。
        :rtype: httpx.Headers
        :author: tongliga
        :date: 2026-10-21
        """
        if isinstance(error, (APIStatusError, httpx.HTTPStatusError)):
            return error.response.headers
        return httpx.Headers()

    @classmethod
    def _retryable(cls, error: Exception) -> bool:
        """根据 HTTP 响应头、状态码或网络错误类型判断是否属于可重试瞬时故障。

        :param error: 捕获的异常对象。
        :type error: Exception
        :return: 是否允许退避重试。
        :rtype: bool
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：优先检查显式 x-should-retry 响应头
        override = cls._headers(error).get("x-should-retry")
        if override is not None:
            if override == "true":
                return True
            if override == "false":
                return False

        # 第二步：检查 HTTP 状态码与配额耗尽关键词
        if isinstance(error, (APIStatusError, httpx.HTTPStatusError)):
            status = error.response.status_code
            if (
                isinstance(error, APIStatusError)
                and (error.code or "").lower() in cls._quota_codes
            ):
                return False
            if status == 429 and cls._quota_text.search(str(error)):
                return False
            return status in (408, 409, 429) or status >= 500

        # 第三步：检查 LangChain 或底层网络异常标记
        if isinstance(error, ModelError):
            return error.is_retryable
        return isinstance(error, (APIConnectionError, httpx.TransportError))

    @classmethod
    def _delay(cls, error: Exception, attempt: int) -> float:
        """计算指数退避等待延迟秒数，并遵循服务端的 Retry-After 建议。

        :param error: 异常对象。
        :type error: Exception
        :param attempt: 当前重试序号。
        :type attempt: int
        :return: 等待秒数。
        :rtype: float
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：计算基础指数抖动退避
        delay = min(2 * 2**attempt, 10) * random.uniform(0.8, 1.2)
        headers = cls._headers(error)

        # 第二步：解析 Retry-After-Ms 或标准 Retry-After
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
                except Exception:
                    continue
            if math.isfinite(minimum):
                delay = max(delay, min(max(0, minimum), 900))
                break
        return math.ceil(delay * 1000) / 1000

    def wrap_model_call(self, request: ModelRequest[Any], handler: Any) -> Any:
        """同步拦截模型请求，执行最多 10 次指数退避重试或部分响应保留。

        :param request: 模型请求对象。
        :type request: langchain.agents.middleware.ModelRequest[typing.Any]
        :param handler: 同步下游处理器。
        :type handler: typing.Any
        :return: 模型响应。
        :rtype: typing.Any
        :author: tongliga
        :date: 2026-10-21
        """
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
                    partial = (
                        self._partial(output, error, attempt + 1, timeouts)
                        if output.delivered
                        else None
                    )
                    if output.delivered and partial is None:
                        raise
                    time.sleep(self._delay(error, attempt))
                    if partial is not None:
                        return partial

    async def awrap_model_call(self, request: ModelRequest[Any], handler: Any) -> Any:
        """异步拦截模型请求，执行最多 10 次指数退避重试或部分响应保留。

        :param request: 模型请求对象。
        :type request: langchain.agents.middleware.ModelRequest[typing.Any]
        :param handler: 异步下游处理器。
        :type handler: typing.Any
        :return: 模型响应。
        :rtype: typing.Any
        :author: tongliga
        :date: 2026-10-21
        """
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
                    partial = (
                        self._partial(output, error, attempt + 1, timeouts)
                        if output.delivered
                        else None
                    )
                    if output.delivered and partial is None:
                        raise
                    await asyncio.sleep(self._delay(error, attempt))
                    if partial is not None:
                        return partial

    @hook_config(can_jump_to=["model"])
    def after_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        """检测到流式中断的部分输出时，自动注入提示词并跳回模型节点接续生成。

        :param state: 图状态。
        :type state: typing.Any
        :param runtime: 中间件运行时。
        :type runtime: typing.Any
        :return: 跳转状态更新字典或 None。
        :rtype: dict[str, typing.Any] | None
        :author: tongliga
        :date: 2026-10-21
        """
        last_message = state["messages"][-1]
        if not (
            last_message.additional_kwargs.get("harness_stream_retry")
            or last_message.additional_kwargs.get("opencode_stream_retry")
        ):
            return None
        return {
            "messages": [
                HumanMessage(
                    content=self._continuation,
                    additional_kwargs={
                        "harness_synthetic": "interrupted-stream",
                        "opencode_synthetic": "interrupted-stream",
                    },
                )
            ],
            "jump_to": "model",
        }

    @hook_config(can_jump_to=["model"])
    async def aafter_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        """异步执行流式接续后置检查。

        :param state: 图状态。
        :type state: typing.Any
        :param runtime: 中间件运行时。
        :type runtime: typing.Any
        :return: 变更字典或 None。
        :rtype: dict[str, typing.Any] | None
        :author: tongliga
        :date: 2026-10-21
        """
        return self.after_model(state, runtime)
