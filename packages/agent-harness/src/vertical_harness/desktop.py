"""桌面端 JSON-RPC 通信协议适配器与智能体事件分发主循环模块。"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from contextlib import aclosing, asynccontextmanager
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any
from uuid import UUID

from langchain_core.messages import HumanMessage
from langgraph.types import Command

from vertical_harness.bridge.messages import DesktopMessages
from vertical_harness.filesystem.resources import HarnessResources
from vertical_harness.bridge.runtime import DesktopRuntime
from vertical_harness.config.checkpoint_config import CheckpointConfig
from vertical_harness.config.langsmith_settings import LangSmithSettings
from vertical_harness.config.settings import HarnessSettings
from vertical_harness.core.compose import AgentComposer
from vertical_harness.core.context import RunContext
from vertical_harness.core.routing import ModelRouting
from vertical_harness.langchain.streaming import AgentStreaming
from vertical_harness.storage.checkpoint import CheckpointFactory
from vertical_harness.storage.store import StoreFactory
from vertical_harness.execution.shell_execution import ShellExecution
from vertical_harness.langchain.tool_details import ToolDetails
from vertical_harness.utils.usage import UsageEstimator

logger = logging.getLogger(__name__)


@dataclass(eq=False)
class AgentLoop:
    """桌面端智能体交互生命周期循环管理器。

    维护当前配置、可信身份、活跃异步任务及并发输出锁。

    :author: tongliga
    :date: 2026-10-22
    """

    settings: HarnessSettings | None = field(default=None, init=False)
    identity: RunContext | None = field(default=None, init=False)
    desktop_options: dict[str, Any] | None = field(default=None, init=False)
    active: dict[str, tuple[str, asyncio.Task[None]]] = field(default_factory=dict, init=False)
    output_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    shell_execution: ShellExecution | None = field(default=None, init=False)

    async def aclose(self) -> None:
        """等待活跃运行完成取消清理，再关闭它们可能创建的后台命令。"""
        tasks = [task for _, task in self.active.values()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self.shell_execution is not None:
            await self.shell_execution.aclose()

    def _context_namespace(self, thread_id: str) -> tuple[str, ...]:
        """构建保存在 BaseStore 中的会话级命名空间元组。

        :param thread_id: 会话线程 ID 字符串。
        :type thread_id: str
        :return: 包含租户、用户与会话 ID 的分层命名空间元组。
        :rtype: tuple[str, ...]
        :author: tongliga
        :date: 2026-10-22
        """
        assert self.identity is not None
        return (
            "vertical_harness",
            "desktop_context",
            self.identity.tenant_id,
            self.identity.user_id,
            thread_id,
        )

    async def context_usage(self, thread_id: str, messages: list[Any]) -> dict[str, Any]:
        """获取或计算特定会话的上下文 Token 用量报告。

        :param thread_id: 会话线程 ID。
        :type thread_id: str
        :param messages: 当前会话的消息列表。
        :type messages: list[typing.Any]
        :return: 包含用量明细与比率的字典报告。
        :rtype: dict[str, typing.Any]
        :author: tongliga
        :date: 2026-10-22
        """
        # 第一步：断言配置已初始化并获取上下文上限
        assert self.settings is not None
        assert self.desktop_options is not None
        maximum = self.desktop_options["max_context_tokens"]

        # 第二步：从持久化 Store 中检索最近一次缓存的用量报告
        async with StoreFactory.aopen_store(self.settings) as store:
            saved = await store.aget(self._context_namespace(thread_id), "usage")

        # 第三步：若无持久化记录，根据现有消息即时估算
        if saved is None:
            return UsageEstimator.usage_estimate(messages, self.settings.model, maximum)

        # 第四步：若存在缓存报告，基于当前配置上限更新剩余量与比率
        report = saved.value
        used = report["used_tokens"]
        report.update(
            max_tokens=maximum,
            remaining_tokens=max(0, maximum - used),
            used_ratio=used / maximum,
            remaining_ratio=max(0, maximum - used) / maximum,
        )
        for section in report["sections"].values():
            if section["tokens"] is not None:
                section["ratio_of_max"] = section["tokens"] / maximum
        return report

    async def dispatch(self, method: str, params: dict[str, Any]) -> Any:
        """分发处理桌面端 JSON-RPC 请求方法。

        :param method: JSON-RPC 方法名（如 'configure', 'harness.inspect', 'run.start', 'run.resume' 等）。
        :type method: str
        :param params: 方法调用的参数字典。
        :type params: dict[str, typing.Any]
        :return: 对应 RPC 方法的执行结果字典或对象。
        :rtype: typing.Any
        :raises ValueError: 当参数缺失、类型错误或方法未知时抛出。
        :raises RuntimeError: 当状态机违规（如已在运行时重复配置）时抛出。
        :author: tongliga
        :date: 2026-10-22
        """
        # 第一步：处理配置 configure 方法
        if method == "configure":
            self.require(params, "tenant_id", "user_id", "workspace_root", "runtime_data_dir", "options")
            if self.active:
                raise RuntimeError("Cannot configure while a run is active")
            if any(
                    not isinstance(params[field], str)
                    for field in ("tenant_id", "user_id", "workspace_root", "runtime_data_dir")
            ):
                raise ValueError("Identity and paths must be strings")
            settings, options = DesktopRuntime.configured_settings(params)
            identity = RunContext(tenant_id=params["tenant_id"], user_id=params["user_id"])
            if self.shell_execution is not None:
                await self.shell_execution.aclose()
            self.shell_execution = ShellExecution(settings.resolved_workspace_root)
            self.settings, self.desktop_options, self.identity = settings, options, identity
            return {"model": settings.model, "environment": DesktopRuntime.runtime_environment(options)}

        # 第二步：查询环境 environment 方法
        if method == "environment":
            return {**DesktopRuntime.environment_defaults(), **DesktopRuntime.runtime_environment(self.desktop_options)}

        # 第三步：优雅关闭 shutdown 方法
        if method == "shutdown":
            await self.aclose()
            return {}

        # 第四步：处理 Harness 资源管理指令（harness.inspect, harness.save, harness.remove, harness.import）
        if method in {"harness.inspect", "harness.save", "harness.remove", "harness.import"}:
            self.require(params, "scope", "workspace_root")
            if method != "harness.inspect" and self.active:
                raise RuntimeError("Cannot edit agent resources while a run is active")
            resources = HarnessResources(
                Path(params["workspace_root"]) if params["workspace_root"] is not None else None
            )
            if method == "harness.inspect":
                return resources.inspect(params["scope"])
            if method == "harness.import":
                self.require(params, "archive_path")
                return resources.import_archive(params["scope"], Path(params["archive_path"]))
            self.require(params, "kind", "name")
            if method == "harness.remove":
                return resources.remove(params["scope"], params["kind"], params["name"])
            self.require(params, "content")
            return resources.save(params["scope"], params["kind"], params["name"], params["content"])

        # 第五步：断言后端已被正确配置
        if self.settings is None or self.identity is None:
            raise RuntimeError("Backend is not configured")

        # 第六步：模型连接测试 model.test 方法
        if method == "model.test":
            response = await ModelRouting.init_configured_model(self.settings).ainvoke("Reply with the word OK.")
            return {"ok": True, "model": self.settings.model, "response": response.content}

        # 第七步：校验会话线程 thread_id 必填
        self.require(params, "thread_id")
        thread_id = self.require_uuid(params["thread_id"], "thread_id")

        # 第八步：会话删除 session.delete 方法
        if method == "session.delete":
            tasks = [task for active_thread, task in self.active.values() if active_thread == thread_id]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if self.shell_execution is not None:
                await self.shell_execution.aclose_session(self.identity.tenant_id, self.identity.user_id, thread_id)
            async with CheckpointFactory.aopen_checkpointer(self.settings) as saver:
                await saver.adelete_thread(thread_id)
            async with StoreFactory.aopen_store(self.settings) as store:
                await store.adelete(self._context_namespace(thread_id), "usage")
            return {"thread_id": thread_id, "deleted": True}

        # 第九步：会话快照 snapshot 与 Token 用量 session.token_usage 方法
        if method in {"snapshot", "session.token_usage"}:
            async with self.open_configured_agent() as agent:
                state = await agent.aget_state(CheckpointConfig.make_checkpoint_config(thread_id))
            messages = state.values.get("messages", [])
            metadata = state.metadata or {}
            usage = await self.context_usage(thread_id, messages)
            if method == "session.token_usage":
                return usage
            interrupts = [
                {"id": item.id, "value": item.value}
                for task in state.tasks
                for item in task.interrupts
            ]
            return {
                "thread_id": thread_id,
                "run_id": metadata.get("desktop_run_id"),
                "messages": [
                    {"type": msg.type, "data": DesktopMessages.raw_message(msg),
                     "message": DesktopMessages.raw_message(msg)}
                    for msg in messages
                    if msg.type in {"human", "ai"}
                ],
                "interrupts": interrupts,
                "artifacts": [],
                "token_usage": usage,
            }

        # 第十步：启动运行 run.start 或恢复执行 run.resume 方法
        if method in {"run.start", "run.resume"}:
            self.require(params, "run_id")
            run_id = self.require_uuid(params["run_id"], "run_id")
            if self.active:
                raise RuntimeError("A run is already active")
            if method == "run.start":
                self.require(params, "text")
                if not isinstance(params["text"], str) or not params["text"].strip():
                    raise ValueError("text must be non-empty")
                message = DesktopMessages.attachment_message(
                    self.settings.resolved_workspace_root,
                    params["text"],
                    params.get("attachments", []),
                )
                inputs: Any = {"messages": [HumanMessage(content=message)], "skills_metadata": None}
            else:
                self.require(params, "decisions")
                async with self.open_configured_agent() as agent:
                    state = await agent.aget_state(CheckpointConfig.make_checkpoint_config(thread_id))
                metadata = state.metadata or {}
                if metadata.get("desktop_run_id") != run_id:
                    raise ValueError("run_id does not match the paused turn")
                if (
                        metadata.get("desktop_user_id") != self.identity.user_id
                        or metadata.get("desktop_tenant_id") != self.identity.tenant_id
                ):
                    raise PermissionError(
                        "Paused turn identity does not match the configured user and tenant"
                    )
                pending = {item.id: item.value for task in state.tasks for item in task.interrupts}
                decisions = params["decisions"]
                if not isinstance(decisions, dict) or set(decisions) != set(pending) or not pending:
                    raise ValueError("Decisions must match all pending interrupts")
                for key, answer in decisions.items():
                    if pending[key].get("kind") == "question":
                        if not isinstance(answer, str) or not answer.strip():
                            raise ValueError("Answers must be nonempty text")
                    elif (
                            not isinstance(answer, dict)
                            or not isinstance(answer.get("decisions"), list)
                            or len(answer["decisions"]) != len(pending[key].get("action_requests", []))
                            or any(
                        not isinstance(item, dict)
                        or set(item) != {"type"}
                        or item["type"] not in {"approve", "reject"}
                        for item in answer["decisions"]
                    )
                    ):
                        raise ValueError("Approval decisions must contain approve or reject items")
                inputs = Command(resume=decisions)

            # 创建后台运行 Task 并注册至 active 活跃表
            task = asyncio.create_task(self._run(thread_id, run_id, inputs))
            self.active[run_id] = (thread_id, task)

            task.add_done_callback(partial(self._finished, run_id))
            return {"run_id": run_id}

        # 第十一步：中止正在运行或恢复挂起的执行 run.stop 方法
        if method == "run.stop":
            self.require(params, "run_id")
            run_id = self.require_uuid(params["run_id"], "run_id")
            current = self.active.get(run_id)
            if current is not None:
                if current[0] != thread_id:
                    raise ValueError("thread_id does not match the active run")
                current[1].cancel()
                if self.shell_execution is not None and self.identity is not None:
                    await self.shell_execution.aclose_session(self.identity.tenant_id, self.identity.user_id, thread_id)
                await asyncio.gather(current[1], return_exceptions=True)
                await self.notify("run.cancelled", {"run_id": run_id, "thread_id": thread_id})
            else:
                async with self.open_configured_agent() as agent:
                    state = await agent.aget_state(CheckpointConfig.make_checkpoint_config(thread_id))
                    if state.tasks and any(task.interrupts for task in state.tasks):
                        if state.metadata.get("desktop_run_id") != run_id:
                            raise ValueError("run_id does not match the paused turn")
                        if (
                                state.metadata.get("desktop_user_id") != self.identity.user_id
                                or state.metadata.get("desktop_tenant_id") != self.identity.tenant_id
                        ):
                            raise PermissionError("Paused turn identity does not match")
                        await agent.aupdate_state(
                            CheckpointConfig.make_checkpoint_config(thread_id), state.values, as_node=state.next[0]
                        )
            return {}
        raise ValueError(f"Unknown method: {method}")

    async def _run(self, thread_id: str, run_id: str, inputs: Any) -> None:
        """执行异步流式智能体循环，监听并推送事件至桌面端。

        :param thread_id: 会话线程唯一标识符。
        :type thread_id: str
        :param run_id: 本次运行唯一标识符。
        :type run_id: str
        :param inputs: 智能体输入数据（新消息字典或恢复 Command）。
        :type inputs: typing.Any
        :return: 无返回值。
        :rtype: None
        :author: tongliga
        :date: 2026-10-22
        """
        terminal: dict[str, Any] | None = None
        cancelled = False
        failure: Exception | None = None
        assert self.desktop_options is not None
        details = ToolDetails(self.desktop_options["max_context_tokens"])
        assert self.identity is not None

        # 第一步：构建包含桌面审计信息的检查点配置
        checkpoint_config = CheckpointConfig.make_checkpoint_config(thread_id)
        checkpoint_config["metadata"] = {
            "desktop_run_id": run_id,
            "desktop_user_id": self.identity.user_id,
            "desktop_tenant_id": self.identity.tenant_id,
        }

        try:
            assert self.settings is not None and self.identity is not None

            # 第二步：打开智能体实例并以 aclosing 安全管理流式事件
            async with (
                self.open_configured_agent() as agent,
                aclosing(
                    AgentStreaming.astream_agent(
                        agent,
                        inputs,
                        config={
                            **checkpoint_config,
                            "run_id": run_id,
                            "recursion_limit": agent.config["recursion_limit"],
                            "callbacks": [details],
                        },
                        context=self.identity,
                        durability=self.settings.durability,
                    )
                ) as events,
            ):
                # 第三步：遍历流式事件并丰富上下文用量与工具明细
                async for event in events:
                    payload = event.model_dump(mode="json")
                    payload["thread_id"] = thread_id

                    # 注入模型调用结束后的 Token 统计报告
                    if (
                            event.type == "model.completed"
                            and event.operation_id in details.context_reports
                    ):
                        report = details.context_reports.pop(event.operation_id)
                        async with StoreFactory.aopen_store(self.settings) as store:
                            await store.aput(self._context_namespace(thread_id), "usage", report)
                        payload["data"]["token_usage"] = report

                    # 注入工具执行的入参与耗时明细
                    if event.type.startswith("tool."):
                        payload["data"].update(details.records.get(event.operation_id, {}))

                    # 捕获终态事件（completed, interrupted, failed）
                    if event.type in {"run.completed", "run.interrupted", "run.failed"}:
                        terminal = payload
                    else:
                        await self.notify("agent.event", payload)

        except asyncio.CancelledError:
            cancelled = True
            raise

        except Exception as exc:
            logger.exception("desktop run failed during setup or streaming")
            failure = exc
            if terminal is None:
                terminal = {
                    "type": "run.failed",
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "seq": 1,
                    "operation_id": run_id,
                    "parent_ids": [],
                    "name": "agent",
                    "agent_name": None,
                    "node": None,
                    "data": {"error_type": type(exc).__name__, "message": str(exc)},
                }

        finally:
            # 第四步：从活跃任务表中移出当前 run
            self.active.pop(run_id, None)
            if terminal is not None and not cancelled:
                await self.notify("agent.event", terminal)

        if failure is not None:
            raise failure

    def _finished(self, run_id: str, done: asyncio.Task[None]) -> None:
        self.active.pop(run_id, None)
        if not done.cancelled() and (error := done.exception()) is not None:
            logger.error("desktop run task ended with error", exc_info=error)

    @asynccontextmanager
    async def open_configured_agent(self) -> AsyncIterator[Any]:
        """根据 AgentLoop 的全局配置与桌面选项打开可用的智能体上下文。

        :param state: 桌面端 AgentLoop 实例。
        :type state: vertical_harness.desktop.AgentLoop
        :return: 异步编译状态图智能体实例迭代器。
        :rtype: collections.abc.AsyncIterator[typing.Any]
        :author: tongliga
        :date: 2026-10-22
        """
        async with AgentComposer.aopen_agent(
            self.settings, options=self.desktop_options, shell_execution=self.shell_execution,
        ) as agent:
            yield agent

    @staticmethod
    def emit(frame: dict[str, Any]) -> None:
        """向标准输出（stdout）打印单行 JSON-RPC 响应或通知帧，并强制刷新。

        :param frame: 待发送的标准 JSON-RPC 数据字典。
        :type frame: dict[str, typing.Any]
        :return: 无返回值。
        :rtype: None
        :author: tongliga
        :date: 2026-10-22
        """
        print(json.dumps(frame, ensure_ascii=False, default=str), flush=True)

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        """在并发输出锁保护下向桌面端推送异步 JSON-RPC 通知消息。

        :param state: 桌面端 AgentLoop 实例。
        :type state: vertical_harness.desktop.AgentLoop
        :param method: 通知方法名称（如 'agent.event', 'run.cancelled'）。
        :type method: str
        :param params: 通知载荷字典。
        :type params: dict[str, typing.Any]
        :return: 无返回值。
        :rtype: None
        :author: tongliga
        :date: 2026-10-22
        """
        async with self.output_lock:
            self.emit({"jsonrpc": "2.0", "method": method, "params": params})

    @staticmethod
    def require(params: dict[str, Any], *fields: str) -> None:
        """断言参数字典中必须包含全部指定的必填键。

        :param params: 待校验的参数字典。
        :type params: dict[str, typing.Any]
        :param fields: 必须存在的键名列表。
        :type fields: str
        :return: 无返回值。
        :rtype: None
        :raises ValueError: 当缺少任何必填键时抛出。
        :author: tongliga
        :date: 2026-10-22
        """
        for name in fields:
            if name not in params:
                raise ValueError(f"Missing required parameter: {name}")

    @staticmethod
    def require_uuid(value: Any, field: str) -> str:
        """断言指定字段值必须为有效的 UUID 格式字符串。

        :param value: 待校验的原始值。
        :type value: typing.Any
        :param field: 字段名称（用于错误提示）。
        :type field: str
        :return: 校验合法的 UUID 字符串。
        :rtype: str
        :raises ValueError: 当值非字符串或无法解析为 UUID 时抛出。
        :author: tongliga
        :date: 2026-10-22
        """
        if not isinstance(value, str):
            raise ValueError(f"{field} must be a UUID string")
        UUID(value)
        return value

    async def main(self) -> None:
        """桌面端守护进程主入口：从 stdin 循环读取 JSON-RPC 指令并异步分发。

        :return: 无返回值。
        :rtype: None
        :author: tongliga
        :date: 2026-10-22
        """
        # 第一步：若启用 LangSmith 追踪，预热探测 Git 版本以防 SDK 自动探测阻塞 stdin
        if LangSmithSettings().tracing and not (
                os.getenv("LANGSMITH_REVISION_ID") or os.getenv("LANGCHAIN_REVISION_ID")
        ):
            try:
                revision = subprocess.run(
                    ["git", "describe", "--tags", "--always", "--dirty"],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=2,
                )
                revision_id = revision.stdout.strip() or "unknown"
            except Exception:
                revision_id = "unknown"
            os.environ["LANGSMITH_REVISION_ID"] = revision_id

        # 第二步：按行异步监听标准输入中的 JSON-RPC 消息
        try:
            while line := await asyncio.to_thread(sys.stdin.readline):
                request = json.loads(line)
                request_id = request.get("id")
                try:
                    # 第三步：分发执行指令并返回结果
                    result = await self.dispatch(request["method"], request.get("params", {}))
                except Exception as exc:
                    logger.exception("JSON-RPC request failed")
                    self.emit(
                        {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32000, "message": str(exc)}}
                    )
                else:
                    self.emit({"jsonrpc": "2.0", "id": request_id, "result": result})
                if request.get("method") == "shutdown":
                    break
        finally:
            # 第四步：进程退出前安全取消所有活跃运行任务并等待清理完成
            await self.aclose()


if __name__ == "__main__":
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    asyncio.run(AgentLoop().main())
