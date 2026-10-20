from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
from contextlib import aclosing, asynccontextmanager
from time import perf_counter
from typing import Any
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import HumanMessage
from langgraph.types import Command

from vertical_harness import (
    HarnessSettings,
    LangSmithSettings,
    RunContext,
    aopen_checkpointer,
    aopen_store,
    astream_agent,
    init_configured_model,
    make_checkpoint_config,
)
from vertical_harness.desktop_runtime import (
    attachment_message,
    configured_settings,
    environment_defaults,
    raw_message,
    runtime_environment,
    usage_estimate,
)
from vertical_harness.engine.compose import aopen_agent

logger = logging.getLogger(__name__)


class AgentLoop:
    def __init__(self) -> None:
        self.settings: HarnessSettings | None = None
        self.identity: RunContext | None = None
        self.desktop_options: dict[str, Any] | None = None
        self.active: dict[str, tuple[str, asyncio.Task[None]]] = {}
        self.output_lock = asyncio.Lock()

    def _context_namespace(self, thread_id: str) -> tuple[str, ...]:
        assert self.identity is not None
        return (
            "vertical_harness",
            "desktop_context",
            self.identity.tenant_id,
            self.identity.user_id,
            thread_id,
        )

    async def context_usage(self, thread_id: str, messages: list[Any]) -> dict[str, Any]:
        assert self.settings is not None
        maximum = (self.desktop_options or {}).get("max_context_tokens", 475_000)
        async with aopen_store(self.settings) as store:
            saved = await store.aget(self._context_namespace(thread_id), "usage")
        if saved is None:
            return usage_estimate(messages, self.settings.model, maximum)
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
        if method == "configure":
            require(params, "tenant_id", "user_id", "workspace_root", "runtime_data_dir", "options")
            if self.active:
                raise RuntimeError("Cannot configure while a run is active")
            if any(
                not isinstance(params[field], str)
                for field in ("tenant_id", "user_id", "workspace_root", "runtime_data_dir")
            ):
                raise ValueError("Identity and paths must be strings")
            settings, options = configured_settings(params)
            identity = RunContext(tenant_id=params["tenant_id"], user_id=params["user_id"])
            self.settings, self.desktop_options, self.identity = settings, options, identity
            return {"model": settings.model, "environment": runtime_environment(options)}
        if method == "environment":
            return {**environment_defaults(), **runtime_environment(self.desktop_options)}
        if method == "shutdown":
            tasks = [task for _, task in self.active.values()]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            return {}
        if self.settings is None or self.identity is None:
            raise RuntimeError("Backend is not configured")
        if method == "model.test":
            response = await init_configured_model(self.settings).ainvoke("Reply with the word OK.")
            return {"ok": True, "model": self.settings.model, "response": response.content}
        require(params, "thread_id")
        thread_id = require_uuid(params["thread_id"], "thread_id")
        if method == "session.delete":
            async with aopen_checkpointer(self.settings) as saver:
                await saver.adelete_thread(thread_id)
            async with aopen_store(self.settings) as store:
                await store.adelete(self._context_namespace(thread_id), "usage")
            return {"thread_id": thread_id, "deleted": True}
        if method in {"snapshot", "session.token_usage"}:
            async with open_configured_agent(self) as agent:
                state = await agent.aget_state(make_checkpoint_config(thread_id))
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
                    {"type": msg.type, "data": raw_message(msg), "message": raw_message(msg)}
                    for msg in messages
                    if msg.type in {"human", "ai"}
                ],
                "interrupts": interrupts,
                "artifacts": [],
                "token_usage": usage,
            }
        if method in {"run.start", "run.resume"}:
            require(params, "run_id")
            run_id = require_uuid(params["run_id"], "run_id")
            if self.active:
                raise RuntimeError("A run is already active")
            if method == "run.start":
                require(params, "text")
                if not isinstance(params["text"], str) or not params["text"].strip():
                    raise ValueError("text must be non-empty")
                message = attachment_message(
                    self.settings.resolved_workspace_root,
                    params["text"],
                    params.get("attachments", []),
                )
                inputs: Any = {"messages": [HumanMessage(content=message)]}
            else:
                require(params, "decisions")
                async with open_configured_agent(self) as agent:
                    state = await agent.aget_state(make_checkpoint_config(thread_id))
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
                async with open_configured_agent(self) as agent:
                    state = await agent.aget_state(make_checkpoint_config(thread_id))
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
            task = asyncio.create_task(self._run(thread_id, run_id, inputs))
            self.active[run_id] = (thread_id, task)

            def finished(done):
                self.active.pop(run_id, None)
                if not done.cancelled() and (error := done.exception()) is not None:
                    logger.error("desktop run task ended with error", exc_info=error)

            task.add_done_callback(finished)
            return {"run_id": run_id}
        if method == "run.stop":
            require(params, "run_id")
            run_id = require_uuid(params["run_id"], "run_id")
            current = self.active.get(run_id)
            if current is not None:
                if current[0] != thread_id:
                    raise ValueError("thread_id does not match the active run")
                current[1].cancel()
                await asyncio.gather(current[1], return_exceptions=True)
                await notify(self, "run.cancelled", {"run_id": run_id, "thread_id": thread_id})
            else:
                async with open_configured_agent(self) as agent:
                    state = await agent.aget_state(make_checkpoint_config(thread_id))
                    if state.tasks and any(task.interrupts for task in state.tasks):
                        if state.metadata.get("desktop_run_id") != run_id:
                            raise ValueError("run_id does not match the paused turn")
                        if (
                            state.metadata.get("desktop_user_id") != self.identity.user_id
                            or state.metadata.get("desktop_tenant_id") != self.identity.tenant_id
                        ):
                            raise PermissionError("Paused turn identity does not match")
                        await agent.aupdate_state(
                            make_checkpoint_config(thread_id), state.values, as_node=state.next[0]
                        )
            return {}
        raise ValueError(f"Unknown method: {method}")

    async def _run(self, thread_id: str, run_id: str, inputs: Any) -> None:

        terminal: dict[str, Any] | None = None

        cancelled = False

        failure: Exception | None = None

        details = ToolDetails((self.desktop_options or {}).get("max_context_tokens", 475_000))

        assert self.identity is not None

        checkpoint_config = make_checkpoint_config(thread_id)
        checkpoint_config["metadata"] = {
            "desktop_run_id": run_id,
            "desktop_user_id": self.identity.user_id,
            "desktop_tenant_id": self.identity.tenant_id,
        }

        try:
            assert self.settings is not None and self.identity is not None

            async with (
                open_configured_agent(self) as agent,
                aclosing(
                    astream_agent(
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
                async for event in events:
                    payload = event.model_dump(mode="json")
                    payload["thread_id"] = thread_id

                    if (
                        event.type == "model.completed"
                        and event.operation_id in details.context_reports
                    ):
                        report = details.context_reports.pop(event.operation_id)
                        async with aopen_store(self.settings) as store:
                            await store.aput(self._context_namespace(thread_id), "usage", report)
                        payload["data"]["token_usage"] = report

                    if event.type.startswith("tool."):
                        payload["data"].update(details.records.get(event.operation_id, {}))

                    if event.type in {"run.completed", "run.interrupted", "run.failed"}:
                        terminal = payload

                    else:
                        await notify(self, "agent.event", payload)

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
            self.active.pop(run_id, None)

            if terminal is not None and not cancelled:
                await notify(self, "agent.event", terminal)

        if failure is not None:
            raise failure


loop = AgentLoop()


class ToolDetails(AsyncCallbackHandler):
    run_inline = True

    def __init__(self, maximum: int = 475_000):
        self.records: dict[str, dict[str, Any]] = {}
        self.started: dict[str, float] = {}
        self.maximum = maximum
        self.context_requests: dict[str, tuple[list[Any], list[Any]]] = {}
        self.context_reports: dict[str, dict[str, Any]] = {}

    async def on_chat_model_start(
        self, serialized, messages, *, run_id, metadata=None, invocation_params=None, **kwargs
    ):
        if (metadata or {}).get("lc_agent_name") == "vertical-agent" and messages:
            self.context_requests[str(run_id)] = (
                messages[0],
                (invocation_params or {}).get("tools", []),
            )

    async def on_llm_end(self, response, *, run_id, **kwargs):
        request = self.context_requests.pop(str(run_id), None)
        if request is None:
            return
        messages, tools = request
        message = response.generations[0][0].message
        usage = message.usage_metadata
        self.context_reports[str(run_id)] = usage_estimate(
            [],
            "",
            self.maximum,
            request_messages=[*messages, message],
            tools=tools,
            reported_tokens=usage["input_tokens"] + usage["output_tokens"] if usage else None,
        )

    async def on_llm_error(self, error, *, run_id, **kwargs):
        self.context_requests.pop(str(run_id), None)

    async def on_tool_start(self, serialized, input_str, *, run_id, inputs=None, **kwargs):
        key = str(run_id)
        self.started[key] = perf_counter()
        self.records[key] = {"arguments": inputs}

    async def on_tool_end(self, output, *, run_id, **kwargs):
        key = str(run_id)
        self.records[key].update(
            {
                "output": output.content,
                "elapsed": perf_counter() - self.started[key],
            }
        )

    async def on_tool_error(self, error, *, run_id, **kwargs):
        key = str(run_id)
        self.records[key].update(
            {
                "output": {"error_type": type(error).__name__},
                "elapsed": perf_counter() - self.started[key],
            }
        )


@asynccontextmanager
async def open_configured_agent(state: AgentLoop):
    async with aopen_agent(state.settings, options=state.desktop_options) as agent:
        yield agent


def emit(frame: dict[str, Any]) -> None:
    print(json.dumps(frame, ensure_ascii=False), flush=True)


async def notify(state: AgentLoop, method: str, params: dict[str, Any]) -> None:
    async with state.output_lock:
        emit({"jsonrpc": "2.0", "method": method, "params": params})


def require(params: dict[str, Any], *fields: str) -> None:
    for field in fields:
        if field not in params:
            raise ValueError(f"Missing required parameter: {field}")


def require_uuid(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a UUID string")
    UUID(value)
    return value


async def main() -> None:
    # The SDK's automatic Git probe inherits stdin and can block the JSON-RPC pipe.
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
        except (OSError, subprocess.SubprocessError):
            revision_id = "unknown"
        os.environ["LANGSMITH_REVISION_ID"] = revision_id

    while line := await asyncio.to_thread(sys.stdin.readline):
        request = json.loads(line)
        request_id = request.get("id")
        try:
            result = await loop.dispatch(request["method"], request.get("params", {}))
        except Exception as exc:
            logger.exception("JSON-RPC request failed")
            emit(
                {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32000, "message": str(exc)}}
            )
        else:
            emit({"jsonrpc": "2.0", "id": request_id, "result": result})
        if request.get("method") == "shutdown":
            break
    tasks = [task for _, task in loop.active.values()]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    asyncio.run(main())
