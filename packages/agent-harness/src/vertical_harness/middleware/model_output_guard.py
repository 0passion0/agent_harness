"""流式模型输出截获与增量 Chunk 状态守卫模块。"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk, GenerationChunk
from langchain_core.tracers.context import register_configure_hook


class ModelOutputGuard(BaseCallbackHandler):
    """模型输出流式捕获器，实时追踪是否已有有效 Token 或工具调用块送达客户端。

    :author: tongliga
    :date: 2026-10-21
    """

    run_inline = True

    def __init__(self) -> None:
        """初始化输出守卫实例状态。

        :author: tongliga
        :date: 2026-10-21
        """
        self.delivered = False
        self.chunk: AIMessageChunk | None = None

    def __enter__(self) -> ModelOutputGuard:
        """进入上下文管理器，在当前异步 ContextVar 中注册自身。

        :return: 守卫实例自身。
        :rtype: ModelOutputGuard
        :author: tongliga
        :date: 2026-10-21
        """
        self._token = _output_guard.set(self)
        return self

    def __exit__(self, *exc_info: Any) -> None:
        """退出上下文管理器，重置 ContextVar 绑定。

        :param exc_info: 异常信息。
        :type exc_info: typing.Any
        :author: tongliga
        :date: 2026-10-21
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

        :param token: 新生成的文本 token 载荷。
        :type token: str | list[str | dict[str, typing.Any]]
        :param chunk: 生成分块对象。
        :type chunk: langchain_core.outputs.GenerationChunk | langchain_core.outputs.ChatGenerationChunk | None
        :param kwargs: 额外上下文参数。
        :type kwargs: typing.Any
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：累计拼接 AIMessageChunk
        if isinstance(chunk, ChatGenerationChunk) and isinstance(chunk.message, AIMessageChunk):
            self.chunk = chunk.message if self.chunk is None else self.chunk + chunk.message

        # 第二步：判定是否已有可感知的有效内容或工具调用交付
        if token or (
            isinstance(chunk, ChatGenerationChunk)
            and isinstance(chunk.message, AIMessageChunk)
            and (chunk.message.tool_call_chunks or any(chunk.message.additional_kwargs.values()))
        ):
            self.delivered = True


_output_guard: ContextVar[ModelOutputGuard | None] = ContextVar("model_output_guard", default=None)
register_configure_hook(_output_guard, inheritable=False)
