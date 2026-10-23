"""配置周期的资源所有者；运行和会话服务只借用这些资源。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.base import BaseStore

from vertical_harness.config.settings import HarnessSettings
from vertical_harness.core.compose import AgentComposer
from vertical_harness.core.context import RunContext
from vertical_harness.execution.shell_execution import ShellExecution
from vertical_harness.log.observability import TracingSession
from vertical_harness.storage.checkpoint import CheckpointFactory
from vertical_harness.storage.store import StoreFactory


class AgentRuntime:
    """资源全部准备成功后才发布；关闭前由调用者等待活跃运行退出。"""

    def __init__(self, settings: HarnessSettings, identity: RunContext) -> None:
        self.settings = settings
        self.identity = identity
        self.shell = ShellExecution(settings.resolved_workspace_root)
        self._stack = AsyncExitStack()
        self.checkpointer: BaseCheckpointSaver[Any]
        self.store: BaseStore

    @classmethod
    async def create(cls, settings: HarnessSettings, identity: RunContext) -> AgentRuntime:
        instance = cls(settings, identity)
        try:
            instance.checkpointer = await instance._stack.enter_async_context(
                CheckpointFactory.aopen_checkpointer(settings)
            )
            instance.store = await instance._stack.enter_async_context(
                StoreFactory.aopen_store(settings)
            )
            instance._stack.push_async_callback(instance.shell.aclose)
        except BaseException:
            await instance._stack.aclose()
            raise
        return instance

    @asynccontextmanager
    async def open_agent(self) -> AsyncIterator[CompiledStateGraph[Any, RunContext, Any, Any]]:
        with TracingSession.tracing_session():
            yield AgentComposer.build_agent(
                self.settings, checkpointer=self.checkpointer, shell_execution=self.shell
            )

    async def aclose(self) -> None:
        await self._stack.aclose()
