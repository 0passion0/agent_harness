"""流式模型输出截获与增量 Chunk 状态守卫模块。"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk, GenerationChunk
from langchain_core.tracers.context import register_configure_hook


class ModelOutputGuard(BaseCallbackHandler):
    """模型输出流式捕获器，实时追踪是否已有有效 Token 或工具调用块送达客户端。"""

    run_inline = True

    def __init__(self) -> None:
        """初始化输出守卫实例状态。"""
        self.delivered = False
        self.chunk: AIMessageChunk | None = None

    def __enter__(self) -> ModelOutputGuard:
        """进入上下文管理器，在当前异步 ContextVar 中注册自身。"""
        self._token = _output_guard.set(self)
        return self

    def __exit__(self, *exc_info: Any) -> None:
        """退出上下文管理器，重置 ContextVar 绑定。"""
        _output_guard.reset(self._token)

    def on_llm_new_token(
        self,
        token: str | list[str | dict[str, Any]],
        *,
        chunk: GenerationChunk | ChatGenerationChunk | None = None,
        **kwargs: Any,
    ) -> None:
        """当大模型生成新的 Token 或增量消息块时触发回调。"""
        if isinstance(chunk, ChatGenerationChunk) and isinstance(chunk.message, AIMessageChunk):
            self.chunk = chunk.message if self.chunk is None else self.chunk + chunk.message
        if token or (
            isinstance(chunk, ChatGenerationChunk)
            and isinstance(chunk.message, AIMessageChunk)
            and (chunk.message.tool_call_chunks or any(chunk.message.additional_kwargs.values()))
        ):
            self.delivered = True


_output_guard: ContextVar[ModelOutputGuard | None] = ContextVar("model_output_guard", default=None)
register_configure_hook(_output_guard, inheritable=False)
