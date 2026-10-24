"""会话快照和上下文用量；删除前由入口取消该会话的运行和后台作业。

本模块提供 `SessionService` 会话服务：
1. 多租户会话命名空间隔离：在 BaseStore 中通过 `(vertical_harness, desktop_context, tenant_id, user_id, thread_id)`
   建立严密的逻辑隔离屏障；
2. Token 用量报告持久化与动态核算：记录最后一次真实模型请求的 Token 分区报告，支持在存储未命中时进行实时估算；
3. 会话完整快照聚合：从 LangGraph Checkpointer 提取历史消息、序列化 AI/Human 消息对、提取未决交互中断并附加上下文用量；
4. 会话物理清空：调用 `adelete_thread` 及 BaseStore `adelete`，彻底销毁指定会话在数据库中的检查点历史与用量缓存。
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from vertical_harness.config.checkpoint_config import CheckpointConfig
from vertical_harness.runtime.agent_runtime import AgentRuntime
from vertical_harness.utils.usage import UsageEstimator


class SessionService:
    """会话状态管理与持久化服务，负责上下文用量存储、状态快照聚合与会话持久数据清除。

    Attributes:
        runtime (AgentRuntime): 持有配置周期存储与检查点资源的运行时引用。
    """

    def __init__(self, runtime: AgentRuntime) -> None:
        """初始化会话服务实例，持有当前配置周期运行时引用。

        Args:
            runtime (AgentRuntime): 运行时宿主对象。
        """
        # 持有配置周期存储与检查点资源的运行时引用
        self.runtime = runtime

    def namespace(self, thread_id: str) -> tuple[str, ...]:
        """构造指定会话在 BaseStore 状态存储中的租户与用户隔离命名空间元组。

        Args:
            thread_id (str): 对话会话线程 ID。

        Returns:
            tuple[str, ...]: 格式如 `("vertical_harness", "desktop_context", tenant_id, user_id, thread_id)` 的命名空间元组。
        """
        identity = self.runtime.identity
        return (
            "vertical_harness",
            "desktop_context",
            identity.tenant_id,
            identity.user_id,
            thread_id,
        )

    def verify_owner(self, metadata: Mapping[str, Any] | None) -> None:
        """校验已有检查点身份，不隐式认领无身份的历史数据。

        :param metadata: 原生检查点元数据；不存在的线程为 None。
        :raises PermissionError: 已有线程缺少身份或属于其他用户。
        """
        if metadata is None:
            return
        identity = self.runtime.identity
        if (
            metadata.get("desktop_tenant_id") != identity.tenant_id
            or metadata.get("desktop_user_id") != identity.user_id
        ):
            raise PermissionError("Thread identity does not match the configured user and tenant")

    async def save_usage(self, thread_id: str, report: dict[str, Any]) -> None:
        """异步将模型调用真实的 Token 用量报告写入状态存储。

        Args:
            thread_id (str): 会话 ID。
            report (dict[str, Any]): Token 估算与真实统计报告字典。
        """
        await self.runtime.store.aput(self.namespace(thread_id), "usage", report)

    async def context_usage(self, thread_id: str, messages: list[Any]) -> dict[str, Any]:
        """获取或基于最新消息估算会话上下文 Token 用量及其相对于预算的占比。

        优先读取 BaseStore 中上一次模型调用落盘的真实用量报告；若未持久化则调用 `UsageEstimator` 进行实时粗算。

        Args:
            thread_id (str): 会话 ID。
            messages (list[Any]): 消息历史列表。

        Returns:
            dict[str, Any]: 结构化 Token 用量快照字典，包含总额、已用、剩余及各分区百分比。
        """
        settings = self.runtime.settings
        maximum = settings.max_context_tokens
        saved = await self.runtime.store.aget(self.namespace(thread_id), "usage")
        if saved is None:
            return UsageEstimator.usage_estimate(messages, settings.model, maximum)
        report = deepcopy(saved.value)
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

    async def snapshot(self, thread_id: str, *, usage_only: bool = False) -> dict[str, Any]:
        """拉取指定会话的状态快照，序列化历史对话消息、待决人机中断与 Token 消耗。

        从 LangGraph Checkpointer 中读取当前状态图快照，提取 Human/AI 消息序列化为 JSON 规范字典。

        Args:
            thread_id (str): 会话 ID。
            usage_only (bool): 若为 True 则仅返回 Token 用量字典，忽略消息与中断详情，默认 False。

        Returns:
            dict[str, Any]: 会话完整状态字典，包含 `thread_id`、`run_id`、`messages`、
                `interrupts`、`artifacts` 与 `token_usage`。
        """
        async with self.runtime.open_agent() as agent:
            state = await agent.aget_state(CheckpointConfig.make_checkpoint_config(thread_id))
            self.verify_owner(state.metadata)
        messages = state.values.get("messages", [])
        usage = await self.context_usage(thread_id, messages)
        if usage_only:
            return usage
        serialized = []
        for message in messages:
            if message.type in {"human", "ai"}:
                data = message.model_dump(mode="json", exclude_none=True)
                serialized.append({"type": message.type, "data": data, "message": data})
        return {
            "thread_id": thread_id,
            "run_id": (state.metadata or {}).get("desktop_run_id"),
            "messages": serialized,
            "interrupts": [
                {"id": item.id, "value": item.value}
                for task in state.tasks
                for item in task.interrupts
            ],
            "artifacts": [],
            "token_usage": usage,
        }

    async def delete(self, thread_id: str) -> dict[str, Any]:
        """彻底清除指定会话的全部持久化状态，包括检查点与状态存储记录。

        Args:
            thread_id (str): 待销毁的会话 ID。

        Returns:
            dict[str, Any]: 确认字典 `{"thread_id": thread_id, "deleted": True}`。
        """
        checkpoint = await self.runtime.checkpointer.aget_tuple(
            CheckpointConfig.make_checkpoint_config(thread_id)
        )
        if checkpoint is not None:
            self.verify_owner(checkpoint.metadata)
        await self.runtime.checkpointer.adelete_thread(thread_id)
        await self.runtime.store.adelete(self.namespace(thread_id), "usage")
        return {"thread_id": thread_id, "deleted": True}
