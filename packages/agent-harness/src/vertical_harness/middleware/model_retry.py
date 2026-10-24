"""大模型调用指数退避重试与中断流式恢复中间件模块。

本模块提供 `ModelRetryMiddleware` 中间件，作为模型调用稳定性的核心防护组件：
1. 异常分类与指数退避：精准识别 408、409、429、5xx 瞬时网络及网关抖动，遵循服务端的 `Retry-After` 头部进行退避；
2. 额度耗尽立即止损：严密识别 `insufficient_quota`、`billing_error` 等欠费错误，杜绝无意义的重复重试；
3. 流式生成意外中断接续：当模型流式生成一半遭遇网络断连时，配合 `ModelOutputGuard` 保留已生成的有效正文片段，
   将其标记为部分响应，并自动注入合成接续提示（`jump_to="model"`）驱动模型无缝补全后续内容。
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
import re
import time
from collections.abc import Mapping
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse, hook_config
from langchain_core.exceptions import ModelError, ModelTimeoutError
from langchain_core.messages import AIMessage, HumanMessage, message_chunk_to_message
from openai import APIConnectionError, APIError, APIStatusError, APITimeoutError

from vertical_harness.middleware.model_output_guard import ModelOutputGuard

logger = logging.getLogger(__name__)


class ModelRetryMiddleware(AgentMiddleware):
    """基于状态码与响应头的智能体模型层指数退避重试中间件，支持流式中断接续。

    配置最多 10 次重试尝试与最多 3 次超时阈值保护。
    """

    # 允许指数退避重试的基础网络与服务异常类型
    _errors: tuple[type[Exception], ...] = (
        ModelError,
        APIError,
        httpx.TransportError,
        httpx.HTTPStatusError,
    )
    # 明确标识网络连接或读取超时的异常类型
    _timeouts: tuple[type[Exception], ...] = (
        ModelTimeoutError,
        APITimeoutError,
        httpx.TimeoutException,
    )
    # 明确标识账户额度耗尽或计费异常的不可重试错误代码
    _quota_codes: tuple[str, ...] = (
        "insufficient_quota",
        "usage_not_included",
        "billing_error",
        "gousagelimiterror",
        "freeusagelimiterror",
        "creditlimitexceeded",
    )
    # 匹配响应中额度超限文本信息的正则表达式，命中则立即终止重试
    _quota_text: re.Pattern[str] = re.compile(
        r"insufficient[-_\s]?(?:quota|balance)|quota[-_\s]?exceeded|budget exceeded|"
        r"usage limit|limit exhausted|package has expired|plan does not yet include",
        re.IGNORECASE,
    )
    # 流式传输中断时提示模型从断点继续生成的合成提示语
    _continuation: str = (
        "The previous response was interrupted. Continue from where you left off "
        "without repeating completed content."
    )

    @staticmethod
    def _retry_state(request: ModelRequest[Any]) -> tuple[int, int]:
        """从历史消息列表中提取上一次重试尝试次数与超时计数。

        Args:
            request (ModelRequest[Any]): 模型调用请求对象。

        Returns:
            tuple[int, int]: 二元组 `(attempt, timeouts)`，表示已执行的重试轮次与累计超时次数。
        """
        for message in reversed(request.messages):
            if isinstance(message, HumanMessage) and message.additional_kwargs.get(
                "harness_synthetic"
            ):
                continue
            if isinstance(message, AIMessage) and (
                retry := message.additional_kwargs.get("harness_stream_retry")
            ):
                return retry["attempt"], retry["timeouts"]
            break
        return 0, 0

    @staticmethod
    def _partial(
        output: ModelOutputGuard, error: Exception, attempt: int, timeouts: int
    ) -> ModelResponse | None:
        """当流式传输中断时，将已交付的部分文本块封装为标记错误的 AIMessage。

        如果已捕获到部分文本生成，且未包含不完整的工具调用，将其转换为标记有 `stream_error` 的模型响应。

        Args:
            output (ModelOutputGuard): 绑定的输出守卫实例。
            error (Exception): 中断抛出的底层异常对象。
            attempt (int): 当前重试轮次序号。
            timeouts (int): 累计超时次数。

        Returns:
            ModelResponse | None: 封装好的部分响应实体；若不满足条件则返回 None 正常抛出异常。
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
        }
        message.response_metadata = {
            **message.response_metadata,
            "finish_reason": "error",
            "stream_error": str(error),
        }
        return ModelResponse(result=[message])

    @staticmethod
    def _headers(error: Exception) -> Mapping[str, str]:
        """从 HTTP 异常对象中提取响应头。

        Args:
            error (Exception): 底层异常对象。

        Returns:
            Mapping[str, str]: HTTP 响应头映射，非 HTTP 异常返回空 Headers。
        """
        if isinstance(error, (APIStatusError, httpx.HTTPStatusError)):
            return error.response.headers
        return httpx.Headers()

    @classmethod
    def _retryable(cls, error: Exception) -> bool:
        """根据 HTTP 响应头、状态码或网络错误类型判断是否属于可重试瞬时故障。

        若服务端返回明确的 `x-should-retry` 响应头则遵循其指令；
        若检测到账户欠费或配额耗尽，坚决返回 False；
        对于 408、409、429、5xx 状态码及底层网络连接超时/异常返回 True。

        Args:
            error (Exception): 捕获的异常。

        Returns:
            bool: 若属于瞬时网络故障或可重试错误则返回 True，否则返回 False。
        """
        if isinstance(error, APIStatusError) and (error.code or "").lower() in cls._quota_codes:
            return False
        if isinstance(error, (APIStatusError, httpx.HTTPStatusError)) and (
            error.response.status_code == 429 and cls._quota_text.search(str(error))
        ):
            return False
        override = cls._headers(error).get("x-should-retry")
        if override is not None:
            if override == "true":
                return True
            if override == "false":
                return False
        if isinstance(error, (APIStatusError, httpx.HTTPStatusError)):
            status = error.response.status_code
            return status in (408, 409, 429) or status >= 500
        if isinstance(error, ModelError):
            return error.is_retryable
        return isinstance(error, (APIConnectionError, httpx.TransportError))

    @classmethod
    def _delay(cls, error: Exception, attempt: int) -> float:
        """计算指数退避等待延迟秒数，并遵循服务端的 Retry-After 建议。

        采用带抖动的指数退避公式：`min(2 * 2**attempt, 10) * uniform(0.8, 1.2)`，
        若服务端提供了 `retry-after-ms` 或 `retry-after`，则以服务端建议为下限。

        Args:
            error (Exception): 触发重试的异常对象。
            attempt (int): 当前已尝试的轮次下标。

        Returns:
            float: 最终计算出的等待延迟时间（秒）。
        """
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
                except Exception:
                    continue
            if math.isfinite(minimum):
                delay = max(delay, min(max(0, minimum), 900))
                break
        return math.ceil(delay * 1000) / 1000

    @classmethod
    def _retry_delay(cls, error: Exception, attempt: int, timeouts: int) -> float:
        """记录可重试故障的元数据，不输出异常正文或请求内容。

        :return: 本次等待秒数；真正的等待保持可取消。
        """
        delay = cls._delay(error, attempt)
        logger.info(
            "model_retrying",
            extra={
                "event_fields": {
                    "attempt": attempt + 1,
                    "timeouts": timeouts,
                    "delay_seconds": delay,
                    "error_type": type(error).__name__,
                }
            },
        )
        return delay

    def wrap_model_call(self, request: ModelRequest[Any], handler: Any) -> Any:
        """同步拦截模型请求，执行最多 10 次指数退避重试或部分响应保留。

        Args:
            request (ModelRequest[Any]): 模型调用请求对象。
            handler (Any): 下一级模型执行处理器。

        Returns:
            Any: 模型调用结果或部分输出响应。

        Raises:
            Exception: 当重试次数耗尽、超时次数超出 3 次或发生不可重试异常时抛出。
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
                    time.sleep(self._retry_delay(error, attempt, timeouts))
                    if partial is not None:
                        return partial

    async def awrap_model_call(self, request: ModelRequest[Any], handler: Any) -> Any:
        """异步拦截模型请求，执行最多 10 次指数退避重试或部分响应保留。

        Args:
            request (ModelRequest[Any]): 异步模型调用请求。
            handler (Any): 下一级异步执行器。

        Returns:
            Any: 异步模型调用结果或部分输出响应。

        Raises:
            Exception: 当重试失败且无法挽回时抛出。
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
                    await asyncio.sleep(self._retry_delay(error, attempt, timeouts))
                    if partial is not None:
                        return partial

    @hook_config(can_jump_to=["model"])
    def after_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        """检测到流式中断的部分输出时，自动注入提示词并跳回模型节点接续生成。

        Args:
            state (Any): 状态字典。
            runtime (Any): 运行时上下文。

        Returns:
            dict[str, Any] | None: 包含续写提示并指示 `jump_to="model"` 的状态更新字典；若非中断重试则返回 None。
        """
        last_message = state["messages"][-1]
        if not last_message.additional_kwargs.get("harness_stream_retry"):
            return None
        return {
            "messages": [
                HumanMessage(
                    content=self._continuation,
                    additional_kwargs={
                        "harness_synthetic": "interrupted-stream",
                    },
                )
            ],
            "jump_to": "model",
        }

    @hook_config(can_jump_to=["model"])
    async def aafter_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        """异步执行流式接续后置检查。

        Args:
            state (Any): 状态字典。
            runtime (Any): 运行时上下文。

        Returns:
            dict[str, Any] | None: 状态更新字典或 None。
        """
        return self.after_model(state, runtime)
