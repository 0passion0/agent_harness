from __future__ import annotations

import asyncio
import json
import logging
import sys
from contextlib import aclosing, asynccontextmanager
from time import perf_counter
from typing import Any
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.load.dump import dumpd
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.types import Command

from vertical_harness import (
    HarnessSettings,
    RunContext,
    aopen_checkpointer,
    aopen_store,
    astream_agent,
    init_configured_model,
    make_checkpoint_config,
)
from vertical_harness.desktop_runtime import (
    aopen_desktop_agent,
    attachment_message,
    configured_settings,
    environment_defaults,
    plugin_catalog,
    raw_message,
    runtime_environment,
    usage_estimate,
)
from vertical_harness.engine.mcp import aopen_mcp_tools
from vertical_harness.engine.policy import PluginAuthorizationGateway

logger = logging.getLogger(__name__)


class AgentLoop:
    def __init__(self) -> None:
        self.settings: HarnessSettings | None = None
        self.identity: RunContext | None = None
        self.desktop_options: dict[str, Any] | None = None
        self.active: dict[str, tuple[str, asyncio.Task[None]]] = {}
        self.output_lock = asyncio.Lock()
        self.auth_gateway: PluginAuthorizationGateway | None = None
        self.turn_id: str | None = None
        self.selected_skill_ids: frozenset[str] = frozenset()
        self.selected_plugin_ids: frozenset[str] = frozenset()
        self.paused_runs: dict[str, str] = {}
        self.plugin_approvals: list[dict[str, Any]] = []

    def _plugin_approval_namespace(self, thread_id: str, run_id: str) -> tuple[str, ...]:
        assert self.identity is not None
        return (
            "vertical_harness",
            "desktop_plugin_approvals",
            self.identity.tenant_id,
            self.identity.user_id,
            thread_id,
            run_id,
        )

    async def _load_plugin_approvals(self, thread_id: str, run_id: str) -> list[dict[str, Any]]:
        assert self.settings is not None
        async with aopen_store(self.settings) as store:
            item = await store.aget(self._plugin_approval_namespace(thread_id, run_id), "scopes")
        return [] if item is None else item.value["approvals"]

    async def _save_plugin_approvals(self, thread_id: str, run_id: str) -> None:
        assert self.settings is not None
        async with aopen_store(self.settings) as store:
            await store.aput(
                self._plugin_approval_namespace(thread_id, run_id),
                "scopes",
                {"approvals": self.plugin_approvals},
            )

    async def _clear_plugin_approvals(self, thread_id: str, run_id: str) -> None:
        assert self.settings is not None
        async with aopen_store(self.settings) as store:
            await store.adelete(self._plugin_approval_namespace(thread_id, run_id), "scopes")

    async def dispatch(self, method: str, params: dict[str, Any]) -> Any:

        if method == "configure":
            require(params, "tenant_id", "user_id", "workspace_root", "runtime_data_dir", "options")

            if self.active:
                raise RuntimeError("Cannot configure while a run is active")

            for field in ("tenant_id", "user_id", "workspace_root", "runtime_data_dir"):
                if not isinstance(params[field], str):
                    raise ValueError(f"{field} must be a string")

            configured, options = configured_settings(params)

            configured_identity = RunContext(
                tenant_id=params["tenant_id"], user_id=params["user_id"]
            )

            self.settings, self.identity, self.desktop_options = (
                configured,
                configured_identity,
                options,
            )

            self.auth_gateway = PluginAuthorizationGateway(
                installed_plugins=configured.enabled_plugins,
                enabled_plugins=configured.enabled_plugins,
            )
            self.plugin_approvals = []

            return {"model": configured.model, "environment": runtime_environment(options)}

        if method == "environment":
            if self.settings is None:
                return {**environment_defaults(), **runtime_environment()}

            return {
                "model": self.settings.model,
                "exa_configured": False,
                "analysis_enabled": False,
                **runtime_environment(self.desktop_options),
            }

        if method == "model.test":
            if self.settings is None:
                raise RuntimeError("Backend is not configured")

            model = init_configured_model(self.settings)

            response = await model.ainvoke("Reply with the word OK.")

            return {"ok": True, "model": self.settings.model, "response": response.content}

        if method == "mcp.test":
            if self.settings is None:
                raise RuntimeError("Backend is not configured")

            async with aopen_mcp_tools(self.settings) as tools:
                return {
                    "servers": [
                        {
                            "name": name,
                            "tools": [
                                tool.name
                                for tool in tools
                                if (tool.tool.metadata or {}).get("harness_mcp_server") == name
                            ],
                        }
                        for name in self.settings.mcp_servers
                    ]
                }

        if method == "session.delete":
            require(params, "thread_id")

            if self.settings is None:
                raise RuntimeError("Backend is not configured")

            thread_id = require_uuid(params["thread_id"], "thread_id")

            async with aopen_checkpointer(self.settings) as checkpointer:
                await checkpointer.adelete_thread(thread_id)

            return {"thread_id": thread_id, "deleted": True}

        if method == "catalog.get":
            if self.settings is None:
                return plugin_catalog()

            return plugin_catalog(self.desktop_options)

        if method == "session.token_usage":
            require(params, "thread_id")

            if self.settings is None:
                raise RuntimeError("Backend is not configured")

            thread_id = require_uuid(params["thread_id"], "thread_id")

            async with open_configured_agent(self) as agent:
                graph_state = await agent.aget_state(make_checkpoint_config(thread_id))

            return usage_estimate(
                graph_state.values.get("messages", []),
                self.settings.model,
                self.desktop_options["max_context_tokens"],
                self.desktop_options,
                self.selected_skill_ids,
            )

        if method == "snapshot":
            require(params, "thread_id")

            if self.settings is None:
                raise RuntimeError("Backend is not configured")

            thread_id = require_uuid(params["thread_id"], "thread_id")

            async with open_configured_agent(self) as agent:
                graph_state = await agent.aget_state(make_checkpoint_config(thread_id))

            values = graph_state.values

            messages = [
                {
                    "type": "human" if msg.type == "human" else "ai",
                    "data": raw_message(msg),
                    "message": raw_message(msg),
                }
                for msg in values.get("messages", [])
                if msg.type in {"human", "ai"}
            ]

            interrupts = [
                {"id": item.id, "value": item.value}
                for task in graph_state.tasks
                for item in task.interrupts
            ]

            token_usage = usage_estimate(
                values.get("messages", []),
                self.settings.model,
                self.desktop_options["max_context_tokens"],
                self.desktop_options,
                self.selected_skill_ids,
            )

            checkpoint_values = graph_state.metadata

            return {
                "thread_id": thread_id,
                "run_id": checkpoint_values.get("desktop_run_id"),
                "messages": messages,
                "interrupts": interrupts,
                "analysis": None,
                "artifacts": [],
                "token_usage": token_usage,
            }

        if method in {"run.start", "run.resume"}:
            require(params, "thread_id", "run_id")

            if self.settings is None or self.identity is None:
                raise RuntimeError("Backend is not configured")

            if self.active:
                raise RuntimeError("A run is already active")

            thread_id = require_uuid(params["thread_id"], "thread_id")

            run_id = require_uuid(params["run_id"], "run_id")

            if method == "run.start":
                require(params, "text", "selected_plugins", "selected_skills")

                if not isinstance(params["selected_plugins"], list) or any(
                    not isinstance(item, str) for item in params["selected_plugins"]
                ):
                    raise ValueError("selected_plugins must be a list of plugin IDs")

                if not isinstance(params["selected_skills"], list) or any(
                    not isinstance(item, str) for item in params["selected_skills"]
                ):
                    raise ValueError("selected_skills must be a list of skill IDs")

                assert self.auth_gateway is not None

                self.auth_gateway.select_for_run(thread_id, run_id, params["selected_plugins"])

                self.plugin_approvals = []

                await self._clear_plugin_approvals(thread_id, run_id)

                self.turn_id = run_id

                self.selected_plugin_ids = frozenset(params["selected_plugins"])

                self.selected_skill_ids = frozenset(params["selected_skills"])

                self.paused_runs.pop(thread_id, None)

                attachments = params.get("attachments", [])

                message = attachment_message(
                    self.settings.resolved_workspace_root, params["text"], attachments
                )

                inputs: Any = {"messages": [HumanMessage(content=message)]}

            else:
                require(params, "decisions")

                decisions = params["decisions"]

                if not isinstance(decisions, dict):
                    raise ValueError(
                        "decisions must map interrupt IDs to an answer or approval decisions"
                    )

                async with open_configured_agent(self) as agent:
                    graph_state = await agent.aget_state(make_checkpoint_config(thread_id))

                checkpoint_values = graph_state.metadata

                saved_run_id = checkpoint_values["desktop_run_id"]

                if saved_run_id != run_id:
                    raise ValueError("run_id does not match the paused turn")

                if (
                    checkpoint_values["desktop_user_id"] != self.identity.user_id
                    or checkpoint_values["desktop_tenant_id"] != self.identity.tenant_id
                ):
                    raise PermissionError(
                        "Paused turn identity does not match the configured user and tenant"
                    )

                selected_plugins = json.loads(checkpoint_values["desktop_selected_plugins"])

                selected_skills = json.loads(checkpoint_values["desktop_selected_skills"])

                saved_approvals = await self._load_plugin_approvals(thread_id, run_id)

                self.selected_plugin_ids = frozenset(selected_plugins)

                self.selected_skill_ids = frozenset(selected_skills)

                self.turn_id = run_id

                assert self.auth_gateway is not None

                self.auth_gateway.select_for_run(thread_id, run_id, selected_plugins)

                self.plugin_approvals = saved_approvals

                for value in self.plugin_approvals:
                    self.auth_gateway.restore_pending_request(
                        value,
                        user_id=self.identity.user_id,
                        tenant_id=self.identity.tenant_id,
                        thread_id=thread_id,
                        run_id=run_id,
                    )
                    self.auth_gateway.approve_request(
                        value["request_id"],
                        user_id=self.identity.user_id,
                        tenant_id=self.identity.tenant_id,
                        run_id=run_id,
                    )

                graph_interrupts = {
                    interrupt_item.id: interrupt_item.value
                    for task in graph_state.tasks
                    for interrupt_item in task.interrupts
                }

                newly_approved: list[dict[str, Any]] = []

                for _interrupt_id, value in graph_interrupts.items():
                    if isinstance(value, dict) and value.get("kind") == "plugin_authorization":
                        self.auth_gateway.restore_pending_request(
                            value,
                            user_id=self.identity.user_id,
                            tenant_id=self.identity.tenant_id,
                            thread_id=thread_id,
                            run_id=run_id,
                        )

                for interrupt_id, decision in decisions.items():
                    if interrupt_id not in graph_interrupts:
                        raise ValueError("Decision does not match a pending interrupt")

                    interrupt_value = graph_interrupts[interrupt_id]

                    if (
                        isinstance(interrupt_value, dict)
                        and interrupt_value.get("kind") == "plugin_authorization"
                    ):
                        if (
                            not isinstance(decision, dict)
                            or decision.get("request_id") != interrupt_value["request_id"]
                        ):
                            raise ValueError(
                                "Plugin authorization decision does not match the pending interrupt"
                            )

                        if (
                            set(decision) != {"request_id", "approved"}
                            or not isinstance(decision["request_id"], str)
                            or not isinstance(decision["approved"], bool)
                        ):
                            raise ValueError(
                                "plugin authorization must contain request_id and approved"
                            )
                        if decision["approved"]:
                            newly_approved.append(interrupt_value)
                    elif isinstance(decision, str):
                        if not decision.strip():
                            raise ValueError("interrupt answers must be non-empty")
                    elif (
                        not isinstance(decision, dict)
                        or not isinstance(decision.get("decisions"), list)
                        or any(
                            not isinstance(item, dict)
                            or item.get("type") not in {"approve", "reject"}
                            for item in decision["decisions"]
                        )
                    ):
                        raise ValueError("approval decisions must contain approve or reject items")

                self.plugin_approvals.extend(newly_approved)

                if newly_approved:
                    await self._save_plugin_approvals(thread_id, run_id)

                inputs = Command(resume=decisions)

            task = asyncio.create_task(self._run(thread_id, run_id, inputs))

            self.active[run_id] = (thread_id, task)

            def finished(done: asyncio.Task[None]) -> None:

                self.active.pop(run_id, None)

                if not done.cancelled() and (error := done.exception()) is not None:
                    logger.error("desktop run task ended with error", exc_info=error)

            task.add_done_callback(finished)

            return {"run_id": run_id}

        if method == "shutdown":
            for _, task in self.active.values():
                task.cancel()

            await asyncio.gather(
                *(task for _, task in self.active.values()), return_exceptions=True
            )

            return {}

        if method == "run.stop":
            require(params, "run_id", "thread_id")

            run_id = require_uuid(params["run_id"], "run_id")

            thread_id = require_uuid(params["thread_id"], "thread_id")

            current = self.active.get(run_id)

            if current is not None:
                active_thread_id, task = current

                if active_thread_id != thread_id:
                    raise ValueError("thread_id does not match the active run")

                task.cancel()

                await asyncio.gather(task, return_exceptions=True)

                await notify(self, "run.cancelled", {"run_id": run_id, "thread_id": thread_id})

            if current is None and self.settings is not None:
                async with open_configured_agent(self) as agent:
                    config = make_checkpoint_config(thread_id)

                    graph_state = await agent.aget_state(config)

                    if graph_state.tasks and any(task.interrupts for task in graph_state.tasks):
                        checkpoint_values = graph_state.metadata
                        if checkpoint_values["desktop_run_id"] != run_id:
                            raise ValueError("run_id does not match the paused turn")
                        if (
                            checkpoint_values["desktop_user_id"] != self.identity.user_id
                            or checkpoint_values["desktop_tenant_id"] != self.identity.tenant_id
                        ):
                            raise PermissionError(
                                "Paused turn identity does not match the configured user and tenant"
                            )

                        await agent.aupdate_state(
                            config, graph_state.values, as_node=graph_state.next[0]
                        )

                        self.paused_runs.pop(thread_id, None)

                        if self.auth_gateway is not None:
                            self.auth_gateway.end_run(thread_id, run_id)

                        if self.turn_id == run_id:
                            self.turn_id = None

                        self.selected_plugin_ids = frozenset()

                        self.selected_skill_ids = frozenset()

                        self.plugin_approvals = []

                        await self._clear_plugin_approvals(thread_id, run_id)

            return {}

        raise ValueError(f"Unknown method: {method}")

    async def _run(self, thread_id: str, run_id: str, inputs: Any) -> None:

        terminal: dict[str, Any] | None = None

        cancelled = False

        failure: Exception | None = None

        details = ToolDetails()

        active_workflows: dict[str, dict[str, Any]] = {}

        last_seq = 0

        turn_id = self.turn_id or run_id

        assert self.identity is not None

        run_metadata = {
            "desktop_run_id": turn_id,
            "desktop_selected_plugins": json.dumps(sorted(self.selected_plugin_ids)),
            "desktop_selected_skills": json.dumps(sorted(self.selected_skill_ids)),
            "desktop_tenant_id": self.identity.tenant_id,
            "desktop_user_id": self.identity.user_id,
        }

        checkpoint_config = make_checkpoint_config(thread_id)

        checkpoint_config["metadata"] = run_metadata

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

                    last_seq = event.seq

                    if event.type.startswith("tool."):
                        payload["data"].update(details.records[event.operation_id])

                    progress = payload.get("data", {})

                    if event.type == "custom.progress" and progress.get("kind") == "workflow":
                        key = event.operation_id

                        if progress["status"] == "running":
                            active_workflows[key] = {
                                **progress,
                                "operation_id": key,
                                "parent_ids": event.parent_ids,
                            }

                        else:
                            active_workflows.pop(key, None)

                    if event.type in {"run.completed", "run.interrupted", "run.failed"}:
                        terminal = payload

                    else:
                        await notify(self, "agent.event", payload)

        except asyncio.CancelledError:
            cancelled = True

            for workflow in active_workflows.values():
                last_seq += 1

                await notify(
                    self,
                    "agent.event",
                    {
                        "type": "custom.progress",
                        "run_id": run_id,
                        "thread_id": thread_id,
                        "seq": last_seq,
                        "operation_id": workflow["operation_id"],
                        "parent_ids": workflow["parent_ids"],
                        "name": workflow["workflow_name"],
                        "agent_name": workflow["workflow_name"],
                        "node": None,
                        "data": {
                            key: workflow[key] for key in ("kind", "plugin_id", "workflow_name")
                        }
                        | {"status": "cancelled"},
                    },
                )

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

            if terminal is not None and terminal.get("type") == "run.interrupted":
                self.paused_runs[thread_id] = turn_id

            elif cancelled or terminal is not None:
                self.paused_runs.pop(thread_id, None)

                if self.auth_gateway is not None:
                    self.auth_gateway.end_run(thread_id, turn_id)

                self.turn_id = None

                self.selected_skill_ids = frozenset()

                self.selected_plugin_ids = frozenset()

                self.plugin_approvals = []

                await self._clear_plugin_approvals(thread_id, turn_id)

            if terminal is not None and not cancelled:
                await notify(self, "agent.event", terminal)

        if failure is not None:
            raise failure


loop = AgentLoop()


class ToolDetails(AsyncCallbackHandler):
    run_inline = True

    def __init__(self):
        self.records: dict[str, dict[str, Any]] = {}
        self.started: dict[str, float] = {}

    async def on_tool_start(self, serialized, input_str, *, run_id, inputs=None, **kwargs):
        key = str(run_id)
        self.started[key] = perf_counter()
        self.records[key] = {"arguments": inputs}

    async def on_tool_end(self, output, *, run_id, **kwargs):
        key = str(run_id)
        self.records[key].update(
            {
                "output": output.content if isinstance(output, ToolMessage) else dumpd(output),
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
    async with aopen_desktop_agent(
        state.settings,
        state.desktop_options,
        state.auth_gateway,
        state.selected_skill_ids,
        state.selected_plugin_ids,
    ) as agent:
        yield agent


def emit(frame: dict[str, Any]) -> None:
    print(json.dumps(frame, ensure_ascii=False), flush=True)


async def notify(state: AgentLoop, method: str, params: dict[str, Any]) -> None:
    async with state.output_lock:
        emit({"jsonrpc": "2.0", "method": method, "params": params})


def text_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "".join(block["text"] for block in content if block.get("type") == "text")


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
