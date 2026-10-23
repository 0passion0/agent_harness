"""会话快照和上下文用量；删除前由入口取消该会话的运行和后台作业。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from vertical_harness.config.checkpoint_config import CheckpointConfig
from vertical_harness.runtime.agent_runtime import AgentRuntime
from vertical_harness.utils.usage import UsageEstimator


class SessionService:
    def __init__(self, runtime: AgentRuntime) -> None:
        self.runtime = runtime

    def namespace(self, thread_id: str) -> tuple[str, ...]:
        identity = self.runtime.identity
        return (
            "vertical_harness",
            "desktop_context",
            identity.tenant_id,
            identity.user_id,
            thread_id,
        )

    async def save_usage(self, thread_id: str, report: dict[str, Any]) -> None:
        await self.runtime.store.aput(self.namespace(thread_id), "usage", report)

    async def context_usage(self, thread_id: str, messages: list[Any]) -> dict[str, Any]:
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
        async with self.runtime.open_agent() as agent:
            state = await agent.aget_state(CheckpointConfig.make_checkpoint_config(thread_id))
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
        await self.runtime.checkpointer.adelete_thread(thread_id)
        await self.runtime.store.adelete(self.namespace(thread_id), "usage")
        return {"thread_id": thread_id, "deleted": True}
