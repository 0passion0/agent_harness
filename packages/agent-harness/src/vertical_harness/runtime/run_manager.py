"""一个活跃运行的启动、审批恢复、取消及终态发布。

本模块提供 `RunManager` 智能体执行调度器：
1. 单一活跃任务约束：严格保证全系统同一时间至多只有一个处于运行状态的 Agent 任务；
2. 会话轮次启动与恢复：支持接收新消息启动轮次（`start`）或提交审批/问答决策恢复中断（`resume`）；
3. 严格身份与中断校验：验证检查点中的 `desktop_run_id`、租户及用户身份一致性，校验审批决策完整性；
4. 优雅取消与清理：支持会话级取消（`cancel` / `stop`），安全回收 Shell 长期作业，并在终态推送 `agent.event`。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import aclosing
from functools import partial
from typing import Any
from uuid import UUID

from langchain_core.messages import HumanMessage
from langgraph.types import Command, StateSnapshot

from vertical_harness.config.checkpoint_config import CheckpointConfig
from vertical_harness.langchain.streaming import AgentStreaming
from vertical_harness.langchain.tool_details import ToolDetails
from vertical_harness.runtime.active_run import ActiveRun
from vertical_harness.runtime.agent_runtime import AgentRuntime
from vertical_harness.runtime.session_service import SessionService

# 模块级日志记录器，记录运行任务失败与取消事件
logger = logging.getLogger(__name__)


class RunManager:
    """单活跃运行管理器，调度智能体对话执行、审批恢复、优雅取消与终态派发。

    Attributes:
        runtime (AgentRuntime): 当前配置周期的运行时宿主。
        sessions (SessionService): 会话持久化与用量统计服务。
        notify (Callable[[str, dict[str, Any]], Awaitable[None]]): 向桌面客户端发送 JSON-RPC 异步事件通知的回调。
    """

    def __init__(
        self,
        runtime: AgentRuntime,
        sessions: SessionService,
        notify: Callable[[str, dict[str, Any]], Awaitable[None]],
    ) -> None:
        """初始化运行管理器，持有配置周期运行时、会话服务与通知推送回调。

        Args:
            runtime (AgentRuntime): 运行时宿主对象。
            sessions (SessionService): 会话管理服务实例。
            notify (Callable[[str, dict[str, Any]], Awaitable[None]]): 外部事件广播回调函数。
        """
        # 配置周期的资源持有者
        self.runtime = runtime
        # 会话持久化与上下文用量服务
        self.sessions = sessions
        # 向桌面端发送 JSON-RPC 通知消息的异步回调
        self.notify = notify
        # 当前全局唯一的活跃运行记录（不变量：同一时间最多仅有一个活跃运行）
        self._active: ActiveRun | None = None

    @property
    def active(self) -> ActiveRun | None:
        """获取当前正在执行的活跃运行记录；无运行进行时返回 None。

        Returns:
            ActiveRun | None: 活跃任务实体或 None。
        """
        return self._active

    def start(self, thread_id: str, run_id: str, message: HumanMessage) -> dict[str, str]:
        """启动一次全新的用户会话对话轮次，创建异步任务并记录单一活跃状态。

        Args:
            thread_id (str): 对话会话线程 ID。
            run_id (str): 本次运行轮次唯一 UUID 字符串。
            message (HumanMessage): 包含文本或多模态附件的输入消息。

        Returns:
            dict[str, str]: 包含 `{"run_id": run_id}` 的字典。

        Raises:
            RuntimeError: 若当前已有正在运行的任务时抛出。
        """
        return self._launch(
            thread_id,
            run_id,
            {
                "messages": [message],
                "skills_metadata": None,
            },
        )

    def _launch(self, thread_id: str, run_id: str, inputs: Any) -> dict[str, str]:
        """内部通用启动入口，校验全局单一活跃运行约束并提交异步事件流任务。

        Args:
            thread_id (str): 会话 ID。
            run_id (str): 运行 ID。
            inputs (Any): 传递给图状态机的初始状态输入或恢复 Command。

        Returns:
            dict[str, str]: 包含 run_id 的启动结果凭据。

        Raises:
            RuntimeError: 当已有正在执行的活跃运行任务时抛出。
        """
        if self._active is not None:
            raise RuntimeError("A run is already active")
        task = asyncio.create_task(self._run(thread_id, run_id, inputs))
        self._active = ActiveRun(thread_id, run_id, task)
        task.add_done_callback(partial(self._finished, run_id))
        return {"run_id": run_id}

    def _verify_paused(self, state: StateSnapshot, run_id: str) -> None:
        """严格校验检查点中断状态与传入的 run_id、租户与用户可信身份是否匹配。

        Args:
            state (StateSnapshot): 检查点中恢复的状态快照对象。
            run_id (str): 待校验恢复的运行 ID。

        Raises:
            ValueError: 当检查点记录的 run_id 与当前请求不匹配时抛出。
            PermissionError: 当检查点记录的用户 ID 或租户 ID 与当前运行时凭据不匹配时抛出。
        """
        metadata = state.metadata or {}
        if metadata.get("desktop_run_id") != run_id:
            raise ValueError("run_id does not match the paused turn")
        identity = self.runtime.identity
        if (
            metadata.get("desktop_user_id") != identity.user_id
            or metadata.get("desktop_tenant_id") != identity.tenant_id
        ):
            raise PermissionError(
                "Paused turn identity does not match the configured user and tenant"
            )

    @staticmethod
    def _validate_decisions(pending: dict[str, Any], decisions: Any) -> None:
        """校验桌面端提交的人机决策数据结构，确保所有未决中断均得到合规答复。

        对于交互式问答（question），必须提供非空文本答案；对于工具执行审批（approval），
        必须为每个动作请求提供 `approve` 或 `reject` 决定。

        Args:
            pending (dict[str, Any]): 检查点中记录的全部未决中断字典，以中断 ID 为键。
            decisions (Any): 前端提交的决策字典，键必须与 pending 完全一致。

        Raises:
            ValueError: 当决策格式不合法或缺失中断应答时抛出。
        """
        if not isinstance(decisions, dict) or set(decisions) != set(pending) or not pending:
            raise ValueError("Decisions must match all pending interrupts")
        for key, answer in decisions.items():
            if pending[key].get("kind") == "question":
                if not isinstance(answer, str) or not answer.strip():
                    raise ValueError("Answers must be nonempty text")
                continue
            if (
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

    async def resume(self, thread_id: str, run_id: str, decisions: Any) -> dict[str, str]:
        """基于审批或交互决策恢复处于中断暂停状态的智能体运行轮次。

        拉取检查点状态并校验身份及决策格式合规后，以 `Command(resume=decisions)` 恢复图执行。

        Args:
            thread_id (str): 对话会话 ID。
            run_id (str): 运行 ID。
            decisions (Any): 决策映射字典。

        Returns:
            dict[str, str]: 包含 `{"run_id": run_id}` 的字典。

        Raises:
            RuntimeError: 若当前已有其他活跃任务运行。
            ValueError / PermissionError: 状态不匹配或未决决策校验失败。
        """
        if self._active is not None:
            raise RuntimeError("A run is already active")
        async with self.runtime.open_agent() as agent:
            state = await agent.aget_state(CheckpointConfig.make_checkpoint_config(thread_id))
        self._verify_paused(state, run_id)
        pending = {item.id: item.value for task in state.tasks for item in task.interrupts}
        self._validate_decisions(pending, decisions)
        return self._launch(thread_id, run_id, Command(resume=decisions))

    async def wait(self) -> None:
        """等待当前活跃运行任务执行完毕。"""
        current = self._active
        if current is not None:
            await current.task

    async def cancel(self, *, thread_id: str | None = None) -> bool:
        """取消指定会话或当前活跃的运行任务，并等待其完全终止后释放槽位。

        Args:
            thread_id (str | None): 可选的限定目标会话 ID；若指定且与当前活跃任务不符则不执行。

        Returns:
            bool: 若成功发出取消信号并释放任务则返回 True，若当前无匹配活跃任务则返回 False。
        """
        current = self._active
        if current is None or (thread_id is not None and current.thread_id != thread_id):
            return False
        current.task.cancel()
        await asyncio.gather(current.task, return_exceptions=True)
        self._release(current.run_id, current.task)
        return True

    async def stop(self, thread_id: str, run_id: str) -> dict[str, Any]:
        """停止运行轮次；若为当前活跃运行则执行取消并广播通知，若为已暂停状态则清除中断。

        Args:
            thread_id (str): 会话 ID。
            run_id (str): 目标运行 ID。

        Returns:
            dict[str, Any]: 空字典。

        Raises:
            ValueError: 若传入的 thread_id 与当前活跃任务不匹配。
        """
        current = self._active
        if current is not None and current.run_id == run_id:
            if current.thread_id != thread_id:
                raise ValueError("thread_id does not match the active run")
            await self.cancel(thread_id=thread_id)
            identity = self.runtime.identity
            await self.runtime.shell.aclose_session(identity.tenant_id, identity.user_id, thread_id)
            await self.notify("run.cancelled", {"run_id": run_id, "thread_id": thread_id})
            return {}
        async with self.runtime.open_agent() as agent:
            config = CheckpointConfig.make_checkpoint_config(thread_id)
            state = await agent.aget_state(config)
            if any(task.interrupts for task in state.tasks):
                self._verify_paused(state, run_id)
                await agent.aupdate_state(config, state.values, as_node=state.next[0])
        return {}

    def _release(self, run_id: str, task: asyncio.Task[None] | None) -> None:
        """安全释放指定 run_id 占用的活跃运行槽位。

        Args:
            run_id (str): 待释放的运行 ID。
            task (asyncio.Task[None] | None): 关联的任务对象。
        """
        if self._active is not None and self._active.run_id == run_id and self._active.task is task:
            self._active = None

    def _finished(self, run_id: str, done: asyncio.Task[None]) -> None:
        """异步运行任务结束时的回调，确保释放活跃槽位并记录未捕获异常。

        Args:
            run_id (str): 运行 ID。
            done (asyncio.Task[None]): 已完成的任务句柄。
        """
        # 任务可能在首次调度前取消，届时 _run 的 finally 尚未进入。
        self._release(run_id, done)
        if not done.cancelled() and (error := done.exception()) is not None:
            logger.error("desktop_run_failed", extra={"error_type": type(error).__name__})

    async def _run(self, thread_id: str, run_id: str, inputs: Any) -> None:
        """核心异步事件流派发执行体，流式转发 UI 事件、采集 Token 用量并在终态安全推送通知。

        配置回调拦截器，实时转发模型增量、工具调用及 Token 消耗分析；终态事件（completed, interrupted, failed）
        统一在流结束时作为最后一个载荷投递。

        Args:
            thread_id (str): 会话 ID。
            run_id (str): 运行 ID。
            inputs (Any): 初始状态或恢复 Command。

        Raises:
            asyncio.CancelledError: 任务被取消时正常重新抛出。
            Exception: 执行过程中发生的未捕获异常。
        """
        terminal: dict[str, Any] | None = None
        cancelled = False
        failure: Exception | None = None
        sequence = 0
        settings, identity = self.runtime.settings, self.runtime.identity
        details = ToolDetails(settings.max_context_tokens)
        config = CheckpointConfig.make_checkpoint_config(thread_id)
        config["metadata"] = {
            "desktop_run_id": run_id,
            "desktop_user_id": identity.user_id,
            "desktop_tenant_id": identity.tenant_id,
        }
        config["run_id"] = UUID(run_id)
        config["callbacks"] = [details]
        try:
            async with self.runtime.open_agent() as agent:
                assert agent.config is not None
                config["recursion_limit"] = agent.config["recursion_limit"]
                async with aclosing(
                    AgentStreaming.astream_agent(
                        agent,
                        inputs,
                        config=config,
                        context=identity,
                        durability=settings.durability,
                    )
                ) as events:
                    async for event in events:
                        sequence = event.seq
                        payload = event.model_dump(mode="json")
                        if (
                            event.type == "model.completed"
                            and event.operation_id in details.context_reports
                        ):
                            report = details.context_reports.pop(event.operation_id)
                            await self.sessions.save_usage(thread_id, report)
                            payload["data"]["token_usage"] = report
                        if event.type.startswith("tool."):
                            payload["data"].update(details.records.get(event.operation_id, {}))
                        if event.type in {"run.completed", "run.interrupted", "run.failed"}:
                            terminal = payload
                        else:
                            await self.notify("agent.event", payload)
        except asyncio.CancelledError:
            cancelled = True
            raise
        except Exception as exc:
            failure = exc
            if terminal is None or terminal["type"] != "run.failed":
                terminal = {
                    "type": "run.failed",
                    "run_id": run_id,
                    "thread_id": thread_id,
                    "seq": sequence + 1,
                    "operation_id": run_id,
                    "parent_ids": [],
                    "name": "agent",
                    "agent_name": None,
                    "node": None,
                    "data": {"error_type": type(exc).__name__},
                }
        finally:
            self._release(run_id, asyncio.current_task())
            if terminal is not None and not cancelled:
                await self.notify("agent.event", terminal)
        if failure is not None:
            raise failure
