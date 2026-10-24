"""流式模型输出截获与增量 Chunk 状态守卫模块。

本模块提供 `ModelOutputGuard` 回调处理器：
1. 实时捕获模型流式生成片段，通过 `chunk` 属性自动累加拼接为完整的 `AIMessageChunk`；
2. 标识首次有效 Token 投递状态（`delivered`），供 `ModelRetryMiddleware` 判定是否发生了“流式输出中途网络中断”；
3. 基于 `ContextVar` 与 LangChain 追踪配置钩子 `register_configure_hook` 实现跨线程隔离自动注入。
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk, GenerationChunk
from langchain_core.tracers.context import register_configure_hook


class ModelOutputGuard(BaseCallbackHandler):
    """模型输出流式捕获器，实时追踪是否已有有效 Token 或工具调用块送达客户端。

    Attributes:
        run_inline (bool): 回调是否在当前线程内同步触发（固定为 True）。
        delivered (bool): 标识当前推理流是否已有非空 Token 或工具调用块派发交付。
        chunk (AIMessageChunk | None): 累计收到的流式消息增量块的总和切片。
    """

    run_inline: bool = True

    def __init__(self) -> None:
        """初始化输出守卫实例状态。"""
        self.delivered: bool = False
        self.chunk: AIMessageChunk | None = None

    def __enter__(self) -> ModelOutputGuard:
        """进入上下文管理器，在当前异步 ContextVar 中注册自身。

        Returns:
            ModelOutputGuard: 守卫实例自身。
        """
        self._token = _output_guard.set(self)
        return self

    def __exit__(self, *exc_info: Any) -> None:
        """退出上下文管理器，重置 ContextVar 绑定。

        Args:
            *exc_info (Any): 异常信息三元组。
        """
        _output_guard.reset(self._token)

    def on_llm_new_token(
        self,
        token: str | list[str | dict[str, Any]],
        *,
        chunk: GenerationChunk | ChatGenerationChunk | None = None,
        **kwargs: Any,
    ) -> None:
        """当大模型生成新的 Token 或增量消息块时触发回调。

        若接收到非空文本 Token、工具调用增量（tool_call_chunks）或元数据，则将 `delivered` 标记置为 True，
        并将增量合并至 `chunk`。

        Args:
            token (str | list[str | dict[str, Any]]): 产生的单个 Token 字符串或结构。
            chunk (GenerationChunk | ChatGenerationChunk | None): 对应的增量生成块。
            **kwargs (Any): 附加元数据。
        """
        if isinstance(chunk, ChatGenerationChunk) and isinstance(chunk.message, AIMessageChunk):
            self.chunk = chunk.message if self.chunk is None else self.chunk + chunk.message
        if token or (
            isinstance(chunk, ChatGenerationChunk)
            and isinstance(chunk.message, AIMessageChunk)
            and (chunk.message.tool_call_chunks or any(chunk.message.additional_kwargs.values()))
        ):
            self.delivered = True


# 协程上下文变量，存放当前活动的模型输出守卫实例
_output_guard: ContextVar[ModelOutputGuard | None] = ContextVar("model_output_guard", default=None)
register_configure_hook(_output_guard, inheritable=False)
