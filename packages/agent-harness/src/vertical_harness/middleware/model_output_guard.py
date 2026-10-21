from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk, GenerationChunk
from langchain_core.tracers.context import register_configure_hook


class ModelOutputGuard(BaseCallbackHandler):
    run_inline = True

    def __init__(self) -> None:
        self.delivered = False
        self.chunk: AIMessageChunk | None = None

    def __enter__(self) -> ModelOutputGuard:
        self._token = _output_guard.set(self)
        return self

    def __exit__(self, *exc_info: Any) -> None:
        _output_guard.reset(self._token)

    def on_llm_new_token(
        self,
        token: str | list[str | dict[str, Any]],
        *,
        chunk: GenerationChunk | ChatGenerationChunk | None = None,
        **kwargs: Any,
    ) -> None:
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
