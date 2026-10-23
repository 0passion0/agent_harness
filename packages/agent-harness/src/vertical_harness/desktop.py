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
    def __init__(self) -> None:
        self.server = StdioServer(self.dispatch, self.aclose)
        self.runtime: AgentRuntime | None = None
        self.runs: RunManager | None = None
        self.sessions: SessionService | None = None

    def _configured(self) -> tuple[AgentRuntime, RunManager, SessionService]:
        if self.runtime is None or self.runs is None or self.sessions is None:
            raise RuntimeError("Backend is not configured")
        return self.runtime, self.runs, self.sessions

    async def aclose(self) -> None:
        if self.runs is not None:
            await self.runs.cancel()
        if self.runtime is not None:
            await self.runtime.aclose()

    async def _configure(self, params: dict[str, Any]) -> dict[str, Any]:
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
        settings = DesktopRuntime.configured_settings(params)
        identity = RunContext(tenant_id=params["tenant_id"], user_id=params["user_id"])
        environment = await asyncio.to_thread(DesktopRuntime.runtime_environment, settings)
        runtime = await AgentRuntime.create(settings, identity)
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
        runtime, runs, sessions = self._configured()
        if method == "model.test":
            response = await ModelRouting.init_configured_model(runtime.settings).ainvoke(
                "Reply with the word OK."
            )
            return {"ok": True, "model": runtime.settings.model, "response": response.content}
        self.require(params, "thread_id")
        thread_id = self.require_uuid(params["thread_id"], "thread_id")
        if method == "session.delete":
            await runs.cancel(thread_id=thread_id)
            identity = runtime.identity
            await runtime.shell.aclose_session(identity.tenant_id, identity.user_id, thread_id)
            return await sessions.delete(thread_id)
        if method in {"snapshot", "session.token_usage"}:
            return await sessions.snapshot(thread_id, usage_only=method == "session.token_usage")
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
        for name in fields:
            if name not in params:
                raise ValueError(f"Missing required parameter: {name}")

    @staticmethod
    def require_uuid(value: Any, field: str) -> str:
        if not isinstance(value, str):
            raise ValueError(f"{field} must be a UUID string")
        UUID(value)
        return value

    async def main(self) -> None:
        DesktopRuntime.prepare_tracing_revision()
        await self.server.serve()


if __name__ == "__main__":
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    asyncio.run(AgentLoop().main())
