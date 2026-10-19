from __future__ import annotations

import os
import shutil
import subprocess
import sys
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langchain_core.messages.utils import count_tokens_approximately
from langgraph.types import interrupt

from vertical_harness import (
    BuildServices,
    HarnessSettings,
    PluginContribution,
    PluginManifest,
    SkillAsset,
    aopen_agent,
    aopen_checkpointer,
    aopen_store,
    build_agent,
    discover_plugins,
)
from vertical_harness.engine.mcp import aopen_mcp_tools


class DesktopPlugin:
    manifest = PluginManifest(plugin_id="desktop.runtime", api_version=1, version="1.0.0",
                              description="Desktop-provided instructions and skills")

    def __init__(self, options: dict[str, Any]):
        self.options = options

    def build(self, _services: BuildServices) -> PluginContribution:
        static_content = self.options["agent_content"] + "\n" + "\n".join(
            skill["content"] for skill in self.options["skills"]
        )
        middleware: list[AgentMiddleware] = [
            ContextLimitMiddleware(self.options["max_context_tokens"], static_content, []),
            DesktopPermissionMiddleware(self.options["permission_mode"]),
        ]
        if self.options["permission_mode"] == "ask":
            middleware.append(DesktopApprovalMiddleware())
        return PluginContribution(
            middleware=middleware,
            skills=[SkillAsset(relative_path=f"desktop/{skill['id']}/SKILL.md", content=skill["content"])
                    for skill in self.options["skills"]],
            instructions=self.options["agent_content"],
        )

class ContextLimitMiddleware(AgentMiddleware):
    """Reject histories whose rough text estimate exceeds the configured limit."""

    def __init__(self, maximum: int, static_content: str, tools: list[Any]):
        self.maximum = maximum
        static_size = len(static_content) + sum(len(item.description or "") for item in tools)
        self.static_tokens = (static_size + 3) // 4
        self.tools = tools

    def before_model(self, state: Any, runtime: Any) -> None:
        estimate = self.static_tokens + count_tokens_approximately(state.get("messages", []))
        if estimate > self.maximum:
            raise ValueError(
                f"Estimated conversation history ({estimate} tokens) exceeds configured "
                f"max_context_tokens ({self.maximum}); history was not sent to the model"
            )


class DesktopPermissionMiddleware(AgentMiddleware):
    """Enforce read-only mode across every tool exposed by the composed agent."""

    _READ_ONLY_TOOLS = frozenset({"read_file", "ls", "glob", "grep"})

    def __init__(self, mode: str):
        self.mode = mode
        self.tools = []

    def wrap_tool_call(self, request: Any, handler: Any) -> Any:
        if self.mode == "read_only" and request.tool.name not in self._READ_ONLY_TOOLS:
            raise PermissionError(f"Tool {request.tool.name} is disabled in read_only mode")
        return handler(request)

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        if self.mode == "read_only" and request.tool.name not in self._READ_ONLY_TOOLS:
            raise PermissionError(f"Tool {request.tool.name} is disabled in read_only mode")
        return await handler(request)


class DesktopApprovalMiddleware(AgentMiddleware):
    """Request native LangGraph approval interrupts before side-effecting tools."""

    def __init__(self):
        self.tools = []

    @staticmethod
    def _approve(request: Any) -> ToolMessage | None:
        name = request.tool.name
        if name not in {"write_file", "edit_file", "execute", "write_todos"}:
            return
        decision = interrupt({"action_requests": [{
            "name": name, "args": request.tool_call.get("args", {}),
        }], "kind": "approval"})
        if not isinstance(decision, dict) or not isinstance(decision.get("decisions"), list):
            raise ValueError("Approval response must contain decisions")
        if not any(item.get("type") == "approve" for item in decision["decisions"]):
            return ToolMessage(
                content="Action rejected by user; the tool was not executed.",
                name=name,
                tool_call_id=request.tool_call["id"],
                status="error",
            )
        return None

    def wrap_tool_call(self, request: Any, handler: Any) -> Any:
        rejected = self._approve(request)
        if rejected is not None:
            return rejected
        return handler(request)

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        rejected = self._approve(request)
        if rejected is not None:
            return rejected
        return await handler(request)



@asynccontextmanager
async def aopen_desktop_agent(settings: HarnessSettings, options: dict[str, Any] | None,
                              default_agent=aopen_agent):
    if options is None:
        async with default_agent(settings) as agent:
            yield agent
        return
    async with AsyncExitStack() as stack:
        store = await stack.enter_async_context(aopen_store(settings))
        checkpointer = await stack.enter_async_context(aopen_checkpointer(settings))
        tools = await stack.enter_async_context(aopen_mcp_tools(settings))
        plugins = [*discover_plugins(settings.enabled_plugins), DesktopPlugin(options)]
        services = BuildServices(settings.resolved_workspace_root, store, settings.plugin_config)
        contributions = {}
        for plugin in plugins:
            if callable(hook := getattr(plugin, "aopen", None)):
                contributions[plugin.manifest.plugin_id] = await stack.enter_async_context(hook(services))
            elif callable(hook := getattr(plugin, "open", None)):
                contributions[plugin.manifest.plugin_id] = stack.enter_context(hook(services))
        yield build_agent(settings, store=store, checkpointer=checkpointer, mcp_tools=tools,
                          plugins=plugins, _contributions=contributions)


def configured_settings(params: dict[str, Any]) -> tuple[HarnessSettings, dict[str, Any]]:
    options = params["options"]
    required = {
        "model_code", "exa_api_key", "runtimes", "permission_mode", "max_context_tokens",
        "agent_content", "skills", "mcp_servers", "analysis_enabled", "exa_enabled",
        "analysis_plugin_dir",
    }
    if set(options) != required:
        raise ValueError("options must contain the declared desktop settings")
    if options["permission_mode"] not in {"read_only", "ask", "full"}:
        raise ValueError("permission_mode must be read_only, ask, or full")
    if not isinstance(options["runtimes"], dict) or set(options["runtimes"]) != {"python", "node"}:
        raise ValueError("runtimes must contain python and node")
    if any(not isinstance(options["runtimes"][key], str) for key in options["runtimes"]):
        raise ValueError("runtime executable paths must be strings")
    if any(not isinstance(skill, dict) or set(skill) != {"id", "content"} for skill in options["skills"]):
        raise ValueError("skills must contain id and content")
    if any(not isinstance(options[key], str) for key in ("model_code", "exa_api_key", "agent_content")):
        raise ValueError("model_code, exa_api_key, and agent_content must be strings")
    if not isinstance(options["analysis_enabled"], bool) or not isinstance(options["exa_enabled"], bool) or not isinstance(options["max_context_tokens"], int):
        raise ValueError("analysis_enabled and max_context_tokens have invalid types")
    if options["max_context_tokens"] < 1:
        raise ValueError("max_context_tokens must be positive")
    if not isinstance(options["mcp_servers"], dict):
        raise ValueError("mcp_servers must be an object")
    if not isinstance(options["analysis_plugin_dir"], str):
        raise ValueError("analysis_plugin_dir must be a string")
    runtime_dir = Path(params["runtime_data_dir"]).expanduser().resolve()
    values = dotenv_values(Path(__file__).parents[1] / ".env")
    base = HarnessSettings.model_validate({
        "model": os.environ.get("VA_MODEL") or values.get("VA_MODEL") or HarnessSettings().model,
    })
    model = options["model_code"] or base.model
    if ":" not in model and model.startswith("deepseek-"):
        model = f"deepseek:{model}"
    elif ":" not in model and model in {"deepseek", "openai", "anthropic", "google_genai"}:
        model = f"{model}:{model}"
    configured = HarnessSettings.model_validate({**base.model_dump(),
        "model": model,
        "workspace_root": Path(params["workspace_root"]),
        "runtime_data_dir": runtime_dir,
        "store_db_path": runtime_dir / "store.sqlite",
        "checkpoint_db_path": runtime_dir / "checkpoints.sqlite",
        "mcp_servers": options["mcp_servers"],
    })
    return configured, {**options}


def environment_defaults() -> dict[str, Any]:
    values = dotenv_values(Path(__file__).parents[1] / ".env")
    model = os.environ.get("VA_MODEL") or values.get("VA_MODEL") or HarnessSettings().model
    return {"model": model, "exa_configured": False, "analysis_enabled": False}


def runtime_environment(options: dict[str, Any] | None = None) -> dict[str, str | None]:
    selected = options or {"runtimes": {"python": "", "node": ""}}
    python = selected["runtimes"]["python"] or sys.executable
    node = selected["runtimes"]["node"] or shutil.which("node")
    node_version = subprocess.run([node, "--version"], capture_output=True, text=True, check=True).stdout.strip() if node else None
    python_version = subprocess.run(
        [python, "--version"], capture_output=True, text=True, check=True,
    ).stdout.strip()
    return {
        "python_executable": python,
        "node_executable": node,
        "python_version": python_version.removeprefix("Python "),
        "node_version": node_version,
        "platform": sys.platform,
    }


def attachment_message(workspace_root: Path, text: str,
                       attachments: list[dict[str, str]]) -> str | list[dict[str, Any]]:
    root = workspace_root.expanduser().resolve()
    validated = []
    for attachment in attachments:
        if not isinstance(attachment, dict) or set(attachment) != {"name", "path", "media_type"}:
            raise ValueError("attachments must contain name, path, and media_type")
        if any(not isinstance(attachment[key], str) for key in attachment):
            raise ValueError("attachment fields must be strings")
        source = Path(attachment["path"])
        if not source.is_absolute():
            raise ValueError("attachment path must be absolute")
        source = source.resolve(strict=True)
        if not source.is_file():
            raise ValueError("attachment path must refer to a file")
        validated.append((attachment, source))
    sources = []
    for attachment, source in validated:
        try:
            source.relative_to(root)
        except ValueError as exc:
            raise ValueError("attachment must be inside the workspace") from exc
        sources.append((attachment, source))
    from attachment_content import content_blocks
    return content_blocks(sources, text, root)


def raw_message(message: Any) -> dict[str, Any]:
    return message.model_dump(mode="json", exclude_none=True)


def usage_estimate(messages: list[Any], model: str, maximum: int = 128_000) -> dict[str, Any]:
    history_tokens = count_tokens_approximately(messages)
    used = min(history_tokens, maximum)
    sections = {name: {"name": name, "tokens": value, "bytes": 0,
                       "ratio_of_used": value / used if used else 0,
                       "ratio_of_max": value / maximum,
                       "description": description}
                for name, value, description in (
                    ("system_prompt", 0, "System prompt estimate unavailable"),
                    ("skills", 0, "Skills estimate unavailable"),
                    ("mcp", 0, "MCP estimate unavailable"),
                    ("memory", 0, "Memory estimate unavailable"),
                    ("history", used, "Provider usage metadata or content-length estimate"))}
    return {"max_tokens": maximum, "used_tokens": used, "remaining_tokens": maximum - used,
            "used_ratio": used / maximum, "remaining_ratio": (maximum - used) / maximum,
            "sections": sections,
            "summary_text": f"Estimated {used:,} / {maximum:,} tokens (history only)"}
