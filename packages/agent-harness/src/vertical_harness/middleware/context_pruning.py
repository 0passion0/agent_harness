"""旧工具结果确定性裁剪中间件，在摘要之前把过大历史结果替换为可恢复路径。

本模块提供 `ContextPruningMiddleware` 中间件，作为请求阶段的上下文动态瘦身机制：
1. 触发与目标阈值控制：当会话 Token 超过 60% 预算门限（`start_ratio`）时，自底向上（从旧到新）检索可修剪的工具消息；
2. 历史结果原子落盘：超长的旧工具返回值被持久化写入沙箱文件系统的 `/large_tool_results` 目录下，并以带有文件路径链接的简短预览替换；
3. 保护近期上下文：保护最近生成的 3 个（`keep_recent_results`）工具结果绝对不被裁剪，保证模型即时反思能力；
4. 目标收敛保证：当裁剪后 Token 总量降低至 40% 门限（`target_ratio`）时适时停止，避免不必要的过度精简。
"""

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

# 模块级日志记录器，记录上下文裁剪事件
logger = logging.getLogger(__name__)

# 已落盘工具结果的固定正文前缀，用于避免同一结果被重复包裹
LARGE_RESULT_MARKER: str = "Tool result too large"


@dataclass(eq=False)
class ContextPruningMiddleware(AgentMiddleware):
    """在摘要之前按确定性规则裁剪旧工具结果的请求预处理中间件。

    Attributes:
        backend (BackendProtocol): 文件系统后端，用于将超长工具文本结果落盘保存为物理文件。
        maximum (int): 宿主配置的单次请求最大上下文 Token 上限。
        start_ratio (float): 触发旧工具结果裁剪的当前上下文 Token 占用比例门限，默认 0.60（60%）。
        target_ratio (float): 裁剪的目标收敛比例门限，默认 0.40（40%）。
        keep_recent_results (int): 保护免于被裁剪的最近工具结果数量，默认保护最近 3 条。
        min_chars (int): 考虑执行裁剪的工具结果最小文本字符长度阈值，默认 400 字符。
        large_tool_results_prefix (str): 存储裁剪后大文本结果文件的虚拟相对目录前缀，默认 `"/large_tool_results"`。
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

        Args:
            message (Any): 待估算的单个 LangChain 消息对象。

        Returns:
            int: 估算出的 Token 数量。
        """
        return count_tokens_approximately([message])

    def _count(self, messages: list[Any], request: ModelRequest[Any]) -> int:
        """估算完整请求消息与工具描述的 Token 总量。

        Args:
            messages (list[Any]): 会话消息列表。
            request (ModelRequest[Any]): 模型请求上下文（包含挂载的工具列表）。

        Returns:
            int: 估算出的请求总 Token 消耗。
        """
        return count_tokens_approximately(messages, tools=request.tools)

    def _prunable(self, message: ToolMessage) -> bool:
        """判断单条工具结果是否值得落盘裁剪。

        仅对文本长度大于 `min_chars` 且尚未包含 `LARGE_RESULT_MARKER` 标记的工具结果执行裁剪。

        Args:
            message (ToolMessage): 待判定的工具返回消息。

        Returns:
            bool: 若满足裁剪条件则返回 True，否则返回 False。
        """
        content = message.content
        if not isinstance(content, str) or len(content) <= self.min_chars:
            return False
        return not content.startswith(LARGE_RESULT_MARKER)

    def _candidates(self, messages: list[Any]) -> list[int]:
        """按从旧到新的顺序返回可裁剪的工具结果位置。

        排除末尾受保护的 `keep_recent_results` 个工具结果。

        Args:
            messages (list[Any]): 当前会话消息列表。

        Returns:
            list[int]: 满足裁剪条件的工具消息在列表中的索引切片。
        """
        results = [index for index, item in enumerate(messages) if isinstance(item, ToolMessage)]
        protected = set(results[-self.keep_recent_results :]) if self.keep_recent_results else set()
        return [
            index for index in results if index not in protected and self._prunable(messages[index])
        ]

    def _report(self, before: int, after: int, pruned: int, limit: int) -> None:
        """记录一次确定性裁剪的结构化观测日志，不包含任何消息正文。

        Args:
            before (int): 裁剪前的估算 Token 总量。
            after (int): 裁剪后的最新估算 Token 总量。
            pruned (int): 本次成功落盘替换的工具结果条数。
            limit (int): 当前请求所允许的输入 Token 上限。
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

        Args:
            request (ModelRequest[Any]): 模型调用请求对象。

        Returns:
            tuple[list[Any], int, int, int]: 四元组 `(messages, before_tokens, after_tokens, pruned_count)`。
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
            assert isinstance(original, ToolMessage)
            replacement = _clip_one_tail_message(
                original, index, self.backend, self.large_tool_results_prefix
            )
            # 预览比原文更长时不替换，避免裁剪反而放大请求。
            if replacement is None or self._message_tokens(replacement) >= self._message_tokens(
                original
            ):
                continue
            messages[position] = replacement
            total -= max(0, self._message_tokens(original) - self._message_tokens(replacement))
            pruned += 1
        return messages, before, total, pruned

    def prepare(self, request: ModelRequest[Any]) -> ModelRequest[Any]:
        """同步执行请求消息视图的确定性旧工具结果落盘与裁剪。

        Args:
            request (ModelRequest[Any]): 原始请求对象。

        Returns:
            ModelRequest[Any]: 替换裁剪过消息列表后的新请求实例（若无裁剪则直接返回原对象）。
        """
        messages, before, after, pruned = self._reduce(request)
        limit = ContextLimitMiddleware.input_limit(request, self.maximum)
        self._report(before, after, pruned, limit)
        return request.override(messages=messages) if pruned else request

    def wrap_model_call(self, request: ModelRequest[Any], handler: Any) -> Any:
        """同步包装模型调用，先执行旧工具结果裁剪再传递至后续中间件。

        Args:
            request (ModelRequest[Any]): 模型请求对象。
            handler (Any): 下一级模型执行处理器。

        Returns:
            Any: 模型调用结果。
        """
        return handler(self.prepare(request))

    async def _areduce(self, request: ModelRequest[Any]) -> tuple[list[Any], int, int, int]:
        """异步落盘裁剪旧工具结果，返回新消息列表与裁剪前后 Token 估算。

        Args:
            request (ModelRequest[Any]): 模型请求实体。

        Returns:
            tuple[list[Any], int, int, int]: 四元组 `(messages, before_tokens, after_tokens, pruned_count)`。
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
            assert isinstance(original, ToolMessage)
            replacement = await _aclip_one_tail_message(
                original, index, self.backend, self.large_tool_results_prefix
            )
            # 预览比原文更长时不替换，避免裁剪反而放大请求。
            if replacement is None or self._message_tokens(replacement) >= self._message_tokens(
                original
            ):
                continue
            messages[position] = replacement
            total -= max(0, self._message_tokens(original) - self._message_tokens(replacement))
            pruned += 1
        return messages, before, total, pruned

    async def aprepare(self, request: ModelRequest[Any]) -> ModelRequest[Any]:
        """异步执行请求消息视图的确定性旧工具结果落盘与裁剪。

        Args:
            request (ModelRequest[Any]): 原始请求对象。

        Returns:
            ModelRequest[Any]: 裁剪后的新请求对象或原始请求。
        """
        messages, before, after, pruned = await self._areduce(request)
        limit = ContextLimitMiddleware.input_limit(request, self.maximum)
        self._report(before, after, pruned, limit)
        return request.override(messages=messages) if pruned else request

    async def awrap_model_call(self, request: ModelRequest[Any], handler: Any) -> Any:
        """异步包装模型调用，先执行异步裁剪再传递至后续中间件。

        Args:
            request (ModelRequest[Any]): 模型调用请求。
            handler (Any): 下一级异步执行器。

        Returns:
            Any: 异步执行结果。
        """
        return await handler(await self.aprepare(request))
