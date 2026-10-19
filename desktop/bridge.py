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
from runtime import (
    aopen_desktop_agent,
    attachment_message,
    configured_settings,
    environment_defaults,
    raw_message,
    runtime_environment,
    usage_estimate,
)

from vertical_harness import (
    HarnessSettings,
    RunContext,
    aopen_agent,
    aopen_checkpointer,
    astream_agent,
    init_configured_model,
    make_checkpoint_config,
)
from vertical_harness.engine.mcp import aopen_mcp_tools

logger = logging.getLogger(__name__)
settings: HarnessSettings | None = None
identity: RunContext | None = None
desktop_options: dict[str, Any] | None = None
active: dict[str, tuple[str, asyncio.Task[None]]] = {}
output_lock = asyncio.Lock()


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
        self.records[key].update({
            "output": output.content if isinstance(output, ToolMessage) else dumpd(output),
            "elapsed": perf_counter() - self.started[key],
        })

    async def on_tool_error(self, error, *, run_id, **kwargs):
        key = str(run_id)
        self.records[key].update({
            "output": {"error_type": type(error).__name__},
            "elapsed": perf_counter() - self.started[key],
        })


@asynccontextmanager
async def open_configured_agent():
    async with aopen_desktop_agent(settings, desktop_options, aopen_agent) as agent:
        yield agent


def emit(frame: dict[str, Any]) -> None:
    print(json.dumps(frame, ensure_ascii=False), flush=True)


async def notify(method: str, params: dict[str, Any]) -> None:
    async with output_lock:
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


async def run_stream(thread_id: str, run_id: str, inputs: Any) -> None:
    terminal: dict[str, Any] | None = None
    cancelled = False
    failure: Exception | None = None
    details = ToolDetails()
    try:
        assert settings is not None and identity is not None
        async with open_configured_agent() as agent, aclosing(astream_agent(
            agent,
            inputs,
            config={**make_checkpoint_config(thread_id), "run_id": run_id,
                    "recursion_limit": agent.config["recursion_limit"], "callbacks": [details]},
            context=identity,
            durability=settings.durability,
        )) as events:
            async for event in events:
                payload = event.model_dump(mode="json")
                if event.type.startswith("tool."):
                    payload["data"].update(details.records[event.operation_id])
                if event.type in {"run.completed", "run.interrupted", "run.failed"}:
                    terminal = payload
                else:
                    await notify("agent.event", payload)
    except asyncio.CancelledError:
        cancelled = True
        raise
    except Exception as exc:
        logger.exception("desktop run failed during setup or streaming")
        failure = exc
        if terminal is None:
            terminal = {
                "type": "run.failed", "run_id": run_id, "thread_id": thread_id,
                "seq": 1, "operation_id": run_id, "parent_ids": [], "name": "agent",
                "agent_name": None, "node": None,
                "data": {"error_type": type(exc).__name__, "message": str(exc)},
            }
    finally:
        active.pop(run_id, None)
        if terminal is not None and not cancelled:
            await notify("agent.event", terminal)
    if failure is not None:
        raise failure


async def dispatch(method: str, params: dict[str, Any]) -> Any:
    global settings, identity, desktop_options
    if method == "configure":
        require(params, "tenant_id", "user_id", "workspace_root", "runtime_data_dir", "options")
        if active:
            raise RuntimeError("Cannot configure while a run is active")
        for field in ("tenant_id", "user_id", "workspace_root", "runtime_data_dir"):
            if not isinstance(params[field], str):
                raise ValueError(f"{field} must be a string")
        configured, options = configured_settings(params)
        configured_identity = RunContext(tenant_id=params["tenant_id"], user_id=params["user_id"])
        settings, identity, desktop_options = configured, configured_identity, options
        return {"model": configured.model, "environment": runtime_environment(options)}

    if method == "environment":
        if settings is None:
            return {**environment_defaults(), **runtime_environment()}
        return {"model": settings.model, "exa_configured": False,
                "analysis_enabled": False,
                **runtime_environment(desktop_options)}

    if method == "model.test":
        if settings is None:
            raise RuntimeError("Backend is not configured")
        model = init_configured_model(settings)
        response = await model.ainvoke("Reply with the word OK.")
        return {"ok": True, "model": settings.model, "response": response.content}

    if method == "mcp.test":
        if settings is None:
            raise RuntimeError("Backend is not configured")
        async with aopen_mcp_tools(settings) as tools:
            return {"servers": [
                {"name": name, "tools": [tool.name for tool in tools
                    if (tool.tool.metadata or {}).get("harness_mcp_server") == name]}
                for name in settings.mcp_servers
            ]}

    if method == "session.delete":
        require(params, "thread_id")
        if settings is None:
            raise RuntimeError("Backend is not configured")
        thread_id = require_uuid(params["thread_id"], "thread_id")
        async with aopen_checkpointer(settings) as checkpointer:
            await checkpointer.adelete_thread(thread_id)
        return {"thread_id": thread_id, "deleted": True}

    if method == "session.token_usage":
        require(params, "thread_id")
        if settings is None:
            raise RuntimeError("Backend is not configured")
        thread_id = require_uuid(params["thread_id"], "thread_id")
        async with open_configured_agent() as agent:
            state = await agent.aget_state(make_checkpoint_config(thread_id))
        return usage_estimate(state.values.get("messages", []), settings.model,
                              desktop_options["max_context_tokens"])

    if method == "snapshot":
        require(params, "thread_id")
        if settings is None:
            raise RuntimeError("Backend is not configured")
        thread_id = require_uuid(params["thread_id"], "thread_id")
        async with open_configured_agent() as agent:
            state = await agent.aget_state(make_checkpoint_config(thread_id))
        values = state.values
        messages = [
            {"type": "human" if msg.type == "human" else "ai",
             "data": raw_message(msg),
             "message": raw_message(msg)}
            for msg in values.get("messages", []) if msg.type in {"human", "ai"}
        ]
        interrupts = [
            {"id": item.id, "value": item.value}
            for task in state.tasks for item in task.interrupts
        ]
        return {"thread_id": thread_id, "messages": messages, "interrupts": interrupts,
                "analysis": None, "artifacts": []}

    if method in {"run.start", "run.resume"}:
        require(params, "thread_id", "run_id")
        if settings is None or identity is None:
            raise RuntimeError("Backend is not configured")
        if active:
            raise RuntimeError("A run is already active")
        thread_id = require_uuid(params["thread_id"], "thread_id")
        run_id = require_uuid(params["run_id"], "run_id")
        if method == "run.start":
            require(params, "text")
            attachments = params.get("attachments", [])
            message = attachment_message(settings.resolved_workspace_root, params["text"], attachments)
            inputs: Any = {"messages": [HumanMessage(content=message)]}
        else:
            require(params, "decisions")
            decisions = params["decisions"]
            if not isinstance(decisions, dict):
                raise ValueError("decisions must map interrupt IDs to an answer or approval decisions")
            for value in decisions.values():
                if isinstance(value, str):
                    if not value.strip():
                        raise ValueError("interrupt answers must be non-empty")
                elif (not isinstance(value, dict) or not isinstance(value.get("decisions"), list)
                      or any(not isinstance(item, dict) or item.get("type") not in {"approve", "reject"}
                             for item in value["decisions"])):
                    raise ValueError("approval decisions must contain approve or reject items")
            inputs = Command(resume=decisions)
        task = asyncio.create_task(run_stream(thread_id, run_id, inputs))
        active[run_id] = (thread_id, task)

        def finished(done: asyncio.Task[None]) -> None:
            active.pop(run_id, None)
            if not done.cancelled() and (error := done.exception()) is not None:
                logger.error("desktop run task ended with error", exc_info=error)

        task.add_done_callback(finished)
        return {"run_id": run_id}

    if method == "shutdown":
        for _, task in active.values():
            task.cancel()
        await asyncio.gather(*(task for _, task in active.values()), return_exceptions=True)
        return {}

    if method == "run.stop":
        require(params, "run_id", "thread_id")
        run_id = require_uuid(params["run_id"], "run_id")
        thread_id = require_uuid(params["thread_id"], "thread_id")
        current = active.get(run_id)
        if current is not None:
            active_thread_id, task = current
            if active_thread_id != thread_id:
                raise ValueError("thread_id does not match the active run")
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await notify("run.cancelled", {"run_id": run_id, "thread_id": thread_id})
        if current is None and settings is not None:
            async with open_configured_agent() as agent:
                config = make_checkpoint_config(thread_id)
                state = await agent.aget_state(config)
                if any(task.interrupts for task in state.tasks):
                    await agent.aupdate_state(config, state.values, as_node=state.next[0])
        return {}


    raise ValueError(f"Unknown method: {method}")


async def main() -> None:
    while line := await asyncio.to_thread(sys.stdin.readline):
        request = json.loads(line)
        request_id = request.get("id")
        try:
            result = await dispatch(request["method"], request.get("params", {}))
        except Exception as exc:
            logger.exception("JSON-RPC request failed")
            emit({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32000, "message": str(exc)}})
        else:
            emit({"jsonrpc": "2.0", "id": request_id, "result": result})
        if request.get("method") == "shutdown":
            break
    tasks = [task for _, task in active.values()]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    asyncio.run(main())
