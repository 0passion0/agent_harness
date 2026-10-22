"""在 Deep Agents 固定摘要位置内先完成模型选择与项目指令加载。"""

from collections.abc import Sequence
from functools import partial
from typing import Any

from deepagents.middleware.summarization import SummarizationMiddleware
from langchain.agents.middleware import AgentMiddleware, ModelRequest


class HarnessSummarizationMiddleware(SummarizationMiddleware):
    """复用原生压缩与检查点，只调整无状态请求准备的执行顺序。"""

    @property
    def name(self) -> str:
        # 同名替换原生槽位，避免外层默认摘要在路由和指令加载之前执行。
        return "SummarizationMiddleware"

    def __init__(
        self, *, request_middleware: Sequence[AgentMiddleware] = (), **kwargs: Any
    ) -> None:
        super().__init__(**kwargs)
        self._request_middleware = tuple(request_middleware)

    def wrap_model_call(self, request: ModelRequest, handler: Any) -> Any:
        """准备完整请求，再交给原生摘要与后续预算检查。"""
        prepared = partial(super().wrap_model_call, handler=handler)
        for middleware in reversed(self._request_middleware):
            prepared = partial(middleware.wrap_model_call, handler=prepared)
        return prepared(request)

    async def awrap_model_call(self, request: ModelRequest, handler: Any) -> Any:
        """异步请求使用相同顺序，保留指令读取与摘要的异步实现。"""
        prepared = partial(super().awrap_model_call, handler=handler)
        for middleware in reversed(self._request_middleware):
            prepared = partial(middleware.awrap_model_call, handler=prepared)
        return await prepared(request)
