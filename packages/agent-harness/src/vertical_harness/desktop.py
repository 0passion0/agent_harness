"""桌面宿主入口：连接协议、配置资源、运行管理和会话服务。"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

from langchain_core.messages import HumanMessage

from vertical_harness.bridge.messages import DesktopMessages
from vertical_harness.bridge.runtime import DesktopRuntime
from vertical_harness.bridge.stdio import StdioServer
from vertical_harness.core.context import RunContext
from vertical_harness.core.routing import ModelRouting
from vertical_harness.filesystem.resources import HarnessResources
from vertical_harness.runtime.agent_runtime import AgentRuntime
from vertical_harness.runtime.run_manager import RunManager
from vertical_harness.runtime.session_service import SessionService


class AgentLoop:
    """桌面宿主调度循环，桥接标准输入输出 JSON-RPC 协议与底层 AgentRuntime、RunManager 和 SessionService。

    Attributes:
        server (StdioServer): 标准输入输出 JSON-RPC 通信服务器实例。
        runtime (AgentRuntime | None): 配置周期的持久化资源持有者（数据库连接、Shell 执行器与可信身份），初始化前为 None。
        runs (RunManager | None): 单次活跃运行调度器，负责协调事件流派发与审批恢复，初始化前为 None。
        sessions (SessionService | None): 会话状态快照与历史用量查询服务，初始化前为 None。
    """

    def __init__(self) -> None:
        """初始化桌面调度循环，绑定协议分发与关闭回调。"""
        # 标准输入输出 JSON-RPC 服务器实例
        self.server = StdioServer(self.dispatch, self.aclose)
        # 配置周期的资源所有者（数据库连接、Shell 执行器与可信身份）
        self.runtime: AgentRuntime | None = None
        # 单次活跃运行管理器
        self.runs: RunManager | None = None
        # 会话状态快照与历史用量服务
        self.sessions: SessionService | None = None

    def _configured(self) -> tuple[AgentRuntime, RunManager, SessionService]:
        """校验当前运行时配置有效性并返回服务三元组。

        Returns:
            tuple[AgentRuntime, RunManager, SessionService]: 当前已就绪的运行时实例、运行管理器与会话服务。

        Raises:
            RuntimeError: 尚未调用 configure 初始化或部分核心服务为空时抛出。
        """
        if self.runtime is None or self.runs is None or self.sessions is None:
            raise RuntimeError("Backend is not configured")
        return self.runtime, self.runs, self.sessions

    async def aclose(self) -> None:
        """异步关闭调度循环，先取消活跃运行，再释放配置周期持有的全部资源。

        Returns:
            None
        """
        # 借用方先结束流迭代并取消运行，最后关闭配置周期资源
        if self.runs is not None:
            await self.runs.cancel()
        if self.runtime is not None:
            await self.runtime.aclose()

    async def _configure(self, params: dict[str, Any]) -> dict[str, Any]:
        """解析并应用新配置，原子替换旧运行时资源；若新配置初始化失败则回滚新资源。

        Args:
            params: 桌面端传入的配置参数字典，结构如下：
                - 'tenant_id' (str): 租户唯一标识符。
                - 'user_id' (str): 用户唯一标识符。
                - 'workspace_root' (str): 工作区本地绝对根路径。
                - 'runtime_data_dir' (str): 运行时数据库隔离存储目录。
                - 'options' (dict): 包含 'model_code', 'runtimes', 'permission_mode',
                  'max_context_tokens', 'agent_content' 的选项字典。

        Returns:
            dict[str, Any]: 包含配置应用结果的响应字典：
                - 'model' (str): 实际生效的模型编码字符串。
                - 'environment' (dict): 宿主 Python 与 Node 运行环境探查结果字典。

        Raises:
            RuntimeError: 当前已有活跃运行正在执行时抛出。
            ValueError: 缺少必要参数或身份/路径字段不是字符串时抛出。
        """
        # 校验必要参数存在且类型正确
        self.require(
            params, "tenant_id", "user_id", "workspace_root", "runtime_data_dir", "options"
        )
        if self.runs is not None and self.runs.active is not None:
            raise RuntimeError("Cannot configure while a run is active")
        if any(
            not isinstance(params[key], str)
            for key in ("tenant_id", "user_id", "workspace_root", "runtime_data_dir")
        ):
            raise ValueError("Identity and paths must be strings")

        # 准备新配置与运行时资源
        settings = DesktopRuntime.configured_settings(params)
        identity = RunContext(tenant_id=params["tenant_id"], user_id=params["user_id"])
        environment = await asyncio.to_thread(DesktopRuntime.runtime_environment, settings)
        runtime = await AgentRuntime.create(settings, identity)

        # 构造成功后原子替换；旧资源关闭失败需及时回收新资源，避免泄漏半初始化配置
        try:
            if self.runtime is not None:
                await self.runtime.aclose()
        except BaseException:
            await runtime.aclose()
            raise

        sessions = SessionService(runtime)
        runs = RunManager(runtime, sessions, self.server.notify)
        self.runtime, self.sessions, self.runs = runtime, sessions, runs
        return {"model": settings.model, "environment": environment}

    async def _resources(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """分发指令（AGENTS.md）与技能（Skills）资源的探测、导入、更新与删除操作。

        Args:
            method: 调用的资源方法名（'harness.inspect', 'harness.save', 'harness.remove', 'harness.import'）。
            params: 包含作用域与资源参数的字典：
                - 'scope' (str): 资源作用域 ('system' 或 'project')。
                - 'workspace_root' (str | None): 工作区根目录路径。
                - 'kind' (str, optional): 资源类型 ('instructions' 或 'skills')。
                - 'name' (str, optional): 资源标识名称。
                - 'content' (str, optional): 待保存的文本内容。
                - 'archive_path' (str, optional): 导入归档包文件路径。

        Returns:
            dict[str, Any]: 操作执行结果数据字典。

        Raises:
            RuntimeError: 在有活跃运行期间尝试修改资源时抛出。
            ValueError: 缺少必填字段时抛出。
        """
        self.require(params, "scope", "workspace_root")
        if method != "harness.inspect" and self.runs is not None and self.runs.active is not None:
            raise RuntimeError("Cannot edit agent resources while a run is active")
        resources = HarnessResources(
            Path(params["workspace_root"]) if params["workspace_root"] is not None else None
        )
        if method == "harness.inspect":
            return await asyncio.to_thread(resources.inspect, params["scope"])
        if method == "harness.import":
            self.require(params, "archive_path")
            return await asyncio.to_thread(
                resources.import_archive, params["scope"], Path(params["archive_path"])
            )
        self.require(params, "kind", "name")
        if method == "harness.remove":
            return await asyncio.to_thread(
                resources.remove, params["scope"], params["kind"], params["name"]
            )
        self.require(params, "content")
        return await asyncio.to_thread(
            resources.save, params["scope"], params["kind"], params["name"], params["content"]
        )

    async def dispatch(self, method: str, params: dict[str, Any]) -> Any:
        """按方法名路由执行桌面端的 JSON-RPC 请求，校验参数并返回结果载荷。

        Args:
            method: JSON-RPC 调用的方法名字符串（如 'run.start', 'configure', 'snapshot' 等）。
            params: 请求携带的参数键值对字典。

        Returns:
            Any: 方法执行对应的结构化数据字典或结果对象。

        Raises:
            ValueError: 调用的方法名未知或参数结构校验失败时抛出。
            RuntimeError: 当前服务状态未初始化或违反互斥约束时抛出。
        """
        if method == "configure":
            return await self._configure(params)
        if method == "environment":
            environment = await asyncio.to_thread(
                DesktopRuntime.runtime_environment, self.runtime.settings if self.runtime else None
            )
            return {**DesktopRuntime.environment_defaults(), **environment}
        if method == "shutdown":
            await self.aclose()
            return {}
        if method in {"harness.inspect", "harness.save", "harness.remove", "harness.import"}:
            return await self._resources(method, params)

        # 后续业务方法要求已完成配置初始化
        runtime, runs, sessions = self._configured()
        if method == "model.test":
            response = await ModelRouting.init_configured_model(runtime.settings).ainvoke(
                "Reply with the word OK."
            )
            return {"ok": True, "model": runtime.settings.model, "response": response.content}
        self.require(params, "thread_id")
        thread_id = self.require_uuid(params["thread_id"], "thread_id")

        # 会话删除：取消活跃运行 -> 结束后台 Shell 作业 -> 清理持久状态
        if method == "session.delete":
            await runs.cancel(thread_id=thread_id)
            identity = runtime.identity
            await runtime.shell.aclose_session(identity.tenant_id, identity.user_id, thread_id)
            return await sessions.delete(thread_id)
        if method in {"snapshot", "session.token_usage"}:
            return await sessions.snapshot(thread_id, usage_only=method == "session.token_usage")

        # 运行生命周期控制（启动、审批恢复、停止）
        if method in {"run.start", "run.resume", "run.stop"}:
            self.require(params, "run_id")
            run_id = self.require_uuid(params["run_id"], "run_id")
            if method == "run.start":
                self.require(params, "text")
                text = params["text"]
                if not isinstance(text, str) or not text.strip():
                    raise ValueError("text must be non-empty")
                if runs.active is not None:
                    raise RuntimeError("A run is already active")
                content = DesktopMessages.attachment_message(
                    runtime.settings.resolved_workspace_root, text, params.get("attachments", [])
                )
                return runs.start(thread_id, run_id, HumanMessage(content=content))
            if method == "run.resume":
                self.require(params, "decisions")
                return await runs.resume(thread_id, run_id, params["decisions"])
            return await runs.stop(thread_id, run_id)
        raise ValueError(f"Unknown method: {method}")

    @staticmethod
    def require(params: dict[str, Any], *fields: str) -> None:
        """校验请求参数字典中是否包含所有必填字段。

        Args:
            params: 待验证的原始输入参数字典。
            *fields: 预期必须存在的键名称列表。

        Raises:
            ValueError: 当任一键缺失于 params 时抛出。
        """
        for name in fields:
            if name not in params:
                raise ValueError(f"Missing required parameter: {name}")

    @staticmethod
    def require_uuid(value: Any, field: str) -> str:
        """校验指定参数值为合法 UUID 格式字符串。

        Args:
            value: 待检测的参数值。
            field: 参数名称，用于错误描述定位。

        Returns:
            str: 格式合规的 UUID 字符串。

        Raises:
            ValueError: 当值不是字符串或无法解析为合法 UUID 时抛出。
        """
        if not isinstance(value, str):
            raise ValueError(f"{field} must be a UUID string")
        UUID(value)
        return value

    async def main(self) -> None:
        """准备分布式链路追踪标识并启动标准输入输出的 JSON-RPC 消息循环。

        Returns:
            None
        """
        DesktopRuntime.prepare_tracing_revision()
        await self.server.serve()


if __name__ == "__main__":
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    asyncio.run(AgentLoop().main())
