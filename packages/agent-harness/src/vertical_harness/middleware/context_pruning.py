"""旧工具结果确定性裁剪中间件，在摘要之前把过大历史结果替换为可恢复路径。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from deepagents.backends.protocol import BackendProtocol
from deepagents.middleware._overflow_clip import (
    _aclip_one_tail_message,
    _build_tool_call_index,
    _clip_one_tail_message,
)
from langchain.agents.middleware import AgentMiddleware, ModelRequest
from langchain_core.messages import ToolMessage
from langchain_core.messages.utils import count_tokens_approximately

from vertical_harness.middleware.context_limit import ContextLimitMiddleware

logger = logging.getLogger(__name__)

LARGE_RESULT_MARKER = "Tool result too large"
"""已落盘工具结果的固定正文前缀，用于避免同一结果被重复包裹。"""

# shortcut: 复用 deepagents 私有裁剪与预览助手（依赖锁定在 0.7.x）；
# 升级 deepagents 时复核 _clip_one_tail_message 的签名与预览文案。


@dataclass(eq=False)
class ContextPruningMiddleware(AgentMiddleware):
    """在摘要之前按确定性规则裁剪旧工具结果的请求预处理中间件。

    只修改本次模型请求的视图：旧工具结果完整落盘后替换为预览与恢复路径，
    最近的工具结果与检查点原始消息保持不变，因此不产生额外模型调用。

    :author: tongliga
    :date: 2026-10-26
    """

    backend: BackendProtocol
    maximum: int
    start_ratio: float = 0.60
    target_ratio: float = 0.40
    keep_recent_results: int = 3
    min_chars: int = 400
    large_tool_results_prefix: str = "/large_tool_results"

    @staticmethod
    def _message_tokens(message: Any) -> int:
        """估算单条消息的 Token 数量，用于增量核算裁剪收益。

        :param message: 单条消息对象。
        :type message: typing.Any
        :return: 该消息的近似 Token 数量。
        :rtype: int
        """
        return count_tokens_approximately([message])

    def _count(self, messages: list[Any], request: ModelRequest[Any]) -> int:
        """估算完整请求消息与工具描述的 Token 总量。

        :param messages: 待估算的消息列表。
        :type messages: list[typing.Any]
        :param request: 模型请求对象，用于提供工具描述。
        :type request: langchain.agents.middleware.ModelRequest[typing.Any]
        :return: 近似 Token 总量。
        :rtype: int
        """
        return count_tokens_approximately(messages, tools=request.tools)

    def _prunable(self, message: ToolMessage) -> bool:
        """判断单条工具结果是否值得落盘裁剪。

        :param message: 待判定的工具结果消息。
        :type message: langchain_core.messages.ToolMessage
        :return: 纯文本、长度足够且尚未落盘时返回 True。
        :rtype: bool
        """
        content = message.content
        if not isinstance(content, str) or len(content) <= self.min_chars:
            return False
        return not content.startswith(LARGE_RESULT_MARKER)

    def _candidates(self, messages: list[Any]) -> list[int]:
        """按从旧到新的顺序返回可裁剪的工具结果位置。

        :param messages: 当前模型请求消息列表。
        :type messages: list[typing.Any]
        :return: 可裁剪位置索引列表，最近 keep_recent_results 条结果始终排除。
        :rtype: list[int]
        """
        results = [index for index, item in enumerate(messages) if isinstance(item, ToolMessage)]
        protected = set(results[-self.keep_recent_results:]) if self.keep_recent_results else set()
        return [
            index
            for index in results
            if index not in protected and self._prunable(messages[index])
        ]

    def _report(self, before: int, after: int, pruned: int, limit: int) -> None:
        """记录一次确定性裁剪的结构化观测日志，不包含任何消息正文。

        :param before: 裁剪前 Token 估算值。
        :type before: int
        :param after: 裁剪后 Token 估算值。
        :type after: int
        :param pruned: 被替换的工具结果条数。
        :type pruned: int
        :param limit: 本次请求生效的输入上限。
        :type limit: int
        :return: 无返回值。
        :rtype: None
        """
        if not pruned:
            return
        logger.info(
            "context_pruned",
            extra={
                "event_fields": {
                    "layer": "pruning",
                    "limit": limit,
                    "tokens_before": before,
                    "tokens_after": after,
                    "tool_results_pruned": pruned,
                }
            },
        )

    def _reduce(self, request: ModelRequest[Any]) -> tuple[list[Any], int, int, int]:
        """同步落盘裁剪旧工具结果，返回新消息列表与裁剪前后 Token 估算。

        :param request: 模型请求对象。
        :type request: langchain.agents.middleware.ModelRequest[typing.Any]
        :return: (消息列表, 裁剪前 Token, 裁剪后 Token, 裁剪条数) 元组。
        :rtype: tuple[list[typing.Any], int, int, int]
        """
        messages = list(request.messages)
        before = self._count(messages, request)
        limit = ContextLimitMiddleware.input_limit(request, self.maximum)
        target = int(limit * self.target_ratio)
        if before <= int(limit * self.start_ratio):
            return messages, before, before, 0
        index = _build_tool_call_index(messages)
        total, pruned = before, 0
        for position in self._candidates(messages):
            if total <= target:
                break
            original = messages[position]
            replacement = _clip_one_tail_message(
                original, index, self.backend, self.large_tool_results_prefix
            )
            # 预览比原文更长时不替换，避免裁剪反而放大请求。
            if replacement is None or self._message_tokens(replacement) >= self._message_tokens(original):
                continue
            messages[position] = replacement
            total -= max(0, self._message_tokens(original) - self._message_tokens(replacement))
            pruned += 1
        return messages, before, total, pruned

    def wrap_model_call(self, request: ModelRequest[Any], handler: Any) -> Any:
        """同步包装模型调用，先裁剪旧工具结果再交给下游摘要与预算检查。

        :param request: 模型请求对象。
        :type request: langchain.agents.middleware.ModelRequest[typing.Any]
        :param handler: 下游调用处理器。
        :type handler: typing.Any
        :return: 模型响应结果。
        :rtype: typing.Any
        """
        messages, before, after, pruned = self._reduce(request)
        limit = ContextLimitMiddleware.input_limit(request, self.maximum)
        self._report(before, after, pruned, limit)
        return handler(request.override(messages=messages) if pruned else request)

    async def _areduce(self, request: ModelRequest[Any]) -> tuple[list[Any], int, int, int]:
        """异步落盘裁剪旧工具结果，返回新消息列表与裁剪前后 Token 估算。

        :param request: 模型请求对象。
        :type request: langchain.agents.middleware.ModelRequest[typing.Any]
        :return: (消息列表, 裁剪前 Token, 裁剪后 Token, 裁剪条数) 元组。
        :rtype: tuple[list[typing.Any], int, int, int]
        """
        messages = list(request.messages)
        before = self._count(messages, request)
        limit = ContextLimitMiddleware.input_limit(request, self.maximum)
        target = int(limit * self.target_ratio)
        if before <= int(limit * self.start_ratio):
            return messages, before, before, 0
        index = _build_tool_call_index(messages)
        total, pruned = before, 0
        for position in self._candidates(messages):
            if total <= target:
                break
            original = messages[position]
            replacement = await _aclip_one_tail_message(
                original, index, self.backend, self.large_tool_results_prefix
            )
            # 预览比原文更长时不替换，避免裁剪反而放大请求。
            if replacement is None or self._message_tokens(replacement) >= self._message_tokens(original):
                continue
            messages[position] = replacement
            total -= max(0, self._message_tokens(original) - self._message_tokens(replacement))
            pruned += 1
        return messages, before, total, pruned

    async def awrap_model_call(self, request: ModelRequest[Any], handler: Any) -> Any:
        """异步包装模型调用，先裁剪旧工具结果再交给下游摘要与预算检查。

        :param request: 模型请求对象。
        :type request: langchain.agents.middleware.ModelRequest[typing.Any]
        :param handler: 下游异步调用处理器。
        :type handler: typing.Any
        :return: 模型响应结果。
        :rtype: typing.Any
        """
        messages, before, after, pruned = await self._areduce(request)
        limit = ContextLimitMiddleware.input_limit(request, self.maximum)
        self._report(before, after, pruned, limit)
        return await handler(request.override(messages=messages) if pruned else request)
