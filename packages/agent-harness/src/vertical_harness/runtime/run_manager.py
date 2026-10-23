"""一个活跃运行的启动、审批恢复、取消及终态发布。"""

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

logger = logging.getLogger(__name__)


class RunManager:
    def __init__(
        self,
        runtime: AgentRuntime,
        sessions: SessionService,
        notify: Callable[[str, dict[str, Any]], Awaitable[None]],
    ) -> None:
        self.runtime = runtime
        self.sessions = sessions
        self.notify = notify
        self._active: ActiveRun | None = None

    @property
    def active(self) -> ActiveRun | None:
        return self._active

    def start(self, thread_id: str, run_id: str, message: HumanMessage) -> dict[str, str]:
        return self._launch(
            thread_id,
            run_id,
            {
                "messages": [message],
                "skills_metadata": None,
            },
        )

    def _launch(self, thread_id: str, run_id: str, inputs: Any) -> dict[str, str]:
        if self._active is not None:
            raise RuntimeError("A run is already active")
        task = asyncio.create_task(self._run(thread_id, run_id, inputs))
        self._active = ActiveRun(thread_id, run_id, task)
        task.add_done_callback(partial(self._finished, run_id))
        return {"run_id": run_id}

    def _verify_paused(self, state: StateSnapshot, run_id: str) -> None:
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
        if self._active is not None:
            raise RuntimeError("A run is already active")
        async with self.runtime.open_agent() as agent:
            state = await agent.aget_state(CheckpointConfig.make_checkpoint_config(thread_id))
        self._verify_paused(state, run_id)
        pending = {item.id: item.value for task in state.tasks for item in task.interrupts}
        self._validate_decisions(pending, decisions)
        return self._launch(thread_id, run_id, Command(resume=decisions))

    async def wait(self) -> None:
        current = self._active
        if current is not None:
            await current.task

    async def cancel(self, *, thread_id: str | None = None) -> bool:
        current = self._active
        if current is None or (thread_id is not None and current.thread_id != thread_id):
            return False
        current.task.cancel()
        await asyncio.gather(current.task, return_exceptions=True)
        self._release(current.run_id, current.task)
        return True

    async def stop(self, thread_id: str, run_id: str) -> dict[str, Any]:
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
        if self._active is not None and self._active.run_id == run_id and self._active.task is task:
            self._active = None

    def _finished(self, run_id: str, done: asyncio.Task[None]) -> None:
        # 任务可能在首次调度前取消，届时 _run 的 finally 尚未进入。
        self._release(run_id, done)
        if not done.cancelled() and (error := done.exception()) is not None:
            logger.error("desktop_run_failed", extra={"error_type": type(error).__name__})

    async def _run(self, thread_id: str, run_id: str, inputs: Any) -> None:
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
