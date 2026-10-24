"""配置周期的资源所有者；运行和会话服务只借用这些资源。

本模块提供 `AgentRuntime` 运行时宿主：
1. 配置周期与资源生命周期绑定：管理当前配置版本下所有的检查点持久化连接（`checkpointer`）、
   全局键值存储（`store`）以及持久运行的 Shell 执行服务（`shell`）；
2. 事务性两阶段初始化：通过 `AsyncExitStack` 管理底层资源，任一资源初始化失败时触发全量安全回滚；
3. 一致性借用接口：通过 `open_agent` 异步上下文管理器以轻量级方式编译状态图，注入追踪会话。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.base import BaseStore

from vertical_harness.core.compose import AgentComposer
from vertical_harness.core.context import RunContext
from vertical_harness.execution.shell_execution import ShellExecution
from vertical_harness.log.observability import TracingSession
from vertical_harness.models import HarnessSettings
from vertical_harness.storage.checkpoint import CheckpointFactory
from vertical_harness.storage.store import StoreFactory


class AgentRuntime:
    """资源全部准备成功后才发布；关闭前由调用者等待活跃运行退出。

    Attributes:
        settings (HarnessSettings): 当前运行时配置周期的已验证宿主设置实例。
        identity (RunContext): 宿主注入的可信租户、用户与调度分级实体。
        shell (ShellExecution): 绑定的 Shell 进程与后台作业执行器。
        checkpointer (BaseCheckpointSaver[Any]): 编译就绪的 LangGraph 检查点持久化后端。
        store (BaseStore): 编译就绪的跨会话状态键值持久化存储器。
    """

    def __init__(self, settings: HarnessSettings, identity: RunContext) -> None:
        """初始化 AgentRuntime 实例，声明配置、可信身份与异步资源退出栈。

        Args:
            settings (HarnessSettings): 宿主全局配置对象。
            identity (RunContext): 可信运行时身份上下文。
        """
        # 已通过验证的宿主运行配置
        self.settings = settings
        # 单次运行注入的可信租户与用户身份
        self.identity = identity
        # 配置周期绑定的 Shell 进程与后台作业执行器
        self.shell = ShellExecution(settings.resolved_workspace_root)
        # 管理存储、检查点与 Shell 生命周期的异步资源栈
        self._stack = AsyncExitStack()
        # 原生 LangGraph 状态检查点持久化保存器
        self.checkpointer: BaseCheckpointSaver[Any]
        # 原生 LangGraph 跨会话状态键值存储器
        self.store: BaseStore

    @classmethod
    async def create(cls, settings: HarnessSettings, identity: RunContext) -> AgentRuntime:
        """异步构建并初始化 AgentRuntime 实例，所有资源准备完整后发布；任一资源失败执行回滚清理。

        在退出栈中依次打开检查点数据库、键值数据库连接，并注册 Shell 执行器的异步清理回调。

        Args:
            settings (HarnessSettings): 宿主全局配置。
            identity (RunContext): 运行时租户身份上下文。

        Returns:
            AgentRuntime: 完整初始化后的运行时宿主实例。

        Raises:
            Exception: 若数据库连接失败或资源初始化中断，自动回滚清理已申请的资源并向上抛出。
        """
        instance = cls(settings, identity)
        try:
            # 依次初始化并持有检查点与状态存储连接
            instance.checkpointer = await instance._stack.enter_async_context(
                CheckpointFactory.aopen_checkpointer(settings)
            )
            instance.store = await instance._stack.enter_async_context(
                StoreFactory.aopen_store(settings)
            )
            instance._stack.push_async_callback(instance.shell.aclose)
        except BaseException:
            # 构造失败回收新资源，避免发布半初始化状态
            await instance._stack.aclose()
            raise
        return instance

    @asynccontextmanager
    async def open_agent(
        self, *, settings: HarnessSettings | None = None
    ) -> AsyncIterator[CompiledStateGraph[Any, RunContext, Any, Any]]:
        """基于当前配置周期的检查点与 Shell 资源，借用构建一次性状态图执行实例。

        在 LangSmith 链路追踪会话内部，调用 `AgentComposer.build_agent` 编译状态图并产出。

        Yields:
            CompiledStateGraph[Any, RunContext, Any, Any]: 可直接调用的编译状态图实例。
        """
        with TracingSession.tracing_session():
            yield AgentComposer.build_agent(
                settings if settings is not None else self.settings,
                checkpointer=self.checkpointer,
                store=self.store,
                shell_execution=self.shell,
            )

    async def aclose(self) -> None:
        """异步清理并关闭当前配置周期持有的所有数据库连接、存储以及后台作业。"""
        await self._stack.aclose()
