from __future__ import annotations

import os
import shutil
import subprocess
import sys
from contextlib import asynccontextmanager
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately
from langgraph.types import interrupt

from vertical_harness import (
    BuildServices,
    DomainPlugin,
    HarnessSettings,
    PluginContribution,
    PluginManifest,
    SkillAsset,
    aopen_agent,
    discover_plugins,
)
from vertical_harness.engine.plugin_loader import PLUGIN_GROUP
from vertical_harness.engine.policy import PluginAuthorizationGateway


class DesktopPlugin(DomainPlugin):
    manifest = PluginManifest(
        plugin_id="desktop.runtime",
        api_version=1,
        version="1.0.0",
        description="Desktop-provided instructions and skills",
    )

    def __init__(
        self,
        options: dict[str, Any],
        selected_skill_ids: frozenset[str] | None = None,
        selected_plugin_ids: frozenset[str] = frozenset(),
    ):
        self.options = options
        self.selected_skill_ids = selected_skill_ids
        self.selected_plugin_ids = selected_plugin_ids

    def build(self, _services: BuildServices) -> PluginContribution:
        selected = [
            skill
            for skill in self.options["skills"]
            if self.selected_skill_ids is None or skill["id"] in self.selected_skill_ids
        ]
        static_content = (
            self.options["agent_content"] + "\n" + "\n".join(skill["content"] for skill in selected)
        )
        selected_workflows = [
            (plugin.manifest.plugin_id, workflow.name)
            for plugin in discover_plugins(sorted(self.selected_plugin_ids))
            for workflow in plugin.build(_services).workflows
        ]
        selected_plugin_instructions = ""
        if selected_workflows:
            capabilities = ", ".join(f"{plugin_id}: {name}" for plugin_id, name in selected_workflows)
            selected_plugin_instructions = (
                "Plugins selected for this user turn: "
                + capabilities
                + ". When the user invokes a selected plugin, delegate to its matching workflow using the task tool."
            )
            static_content += "\n\n" + selected_plugin_instructions
        middleware: list[AgentMiddleware] = [
            ContextLimitMiddleware(self.options["max_context_tokens"], static_content, []),
            DesktopPermissionMiddleware(self.options["permission_mode"]),
        ]
        if self.options["permission_mode"] == "ask":
            middleware.append(DesktopApprovalMiddleware())
        return PluginContribution(
            middleware=middleware,
            skills=[
                SkillAsset(
                    relative_path=f"desktop/{skill['id']}/SKILL.md", content=skill["content"]
                )
                for skill in selected
            ],
            instructions="\n".join(
                part
                for part in (self.options["agent_content"], selected_plugin_instructions)
                if part
            ),
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
        decision = interrupt(
            {
                "action_requests": [
                    {
                        "name": name,
                        "args": request.tool_call.get("args", {}),
                    }
                ],
                "kind": "approval",
            }
        )
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
async def aopen_desktop_agent(
    settings: HarnessSettings,
    options: dict[str, Any] | None,
    auth_gateway: PluginAuthorizationGateway | None = None,
    selected_skill_ids: frozenset[str] | None = None,
    selected_plugin_ids: frozenset[str] = frozenset(),
):
    if options is None:
        async with aopen_agent(settings) as agent:
            yield agent
        return
    plugins = [
        *discover_plugins(settings.enabled_plugins),
        DesktopPlugin(options, selected_skill_ids, selected_plugin_ids),
    ]
    async with aopen_agent(
        settings, plugins=plugins, auth_gateway=auth_gateway, selected_skill_ids=selected_skill_ids
    ) as agent:
        yield agent


def plugin_catalog(options: dict[str, Any] | None = None) -> dict[str, list[dict[str, Any]]]:
    plugins: list[dict[str, Any]] = []
    skills: list[dict[str, Any]] = []
    ids = sorted({entry.name for entry in entry_points(group=PLUGIN_GROUP)})
    for plugin in discover_plugins(ids):
        contribution = plugin.build(BuildServices(workspace_root=Path.cwd()))
        manifest = plugin.manifest
        plugins.append(
            {
                "id": manifest.plugin_id,
                "name": manifest.plugin_id,
                "description": manifest.description,
                "version": manifest.version,
                "workflows": [
                    {"name": workflow.name, "description": workflow.description}
                    for workflow in contribution.workflows
                ],
            }
        )
        skills.extend(
            {
                "id": f"{manifest.plugin_id}:{skill.relative_path}",
                "name": skill.relative_path,
                "description": "",
                "source": manifest.plugin_id,
                "plugin_id": manifest.plugin_id,
            }
            for skill in contribution.skills
        )
    for skill in (options or {}).get("skills", []):
        skills.append(
            {
                "id": skill["id"],
                "name": skill["id"],
                "description": "",
                "source": skill.get("source", "imported"),
            }
        )
    return {"plugins": plugins, "skills": skills}


def configured_settings(params: dict[str, Any]) -> tuple[HarnessSettings, dict[str, Any]]:
    options = params["options"]
    required = {
        "model_code",
        "runtimes",
        "permission_mode",
        "max_context_tokens",
        "agent_content",
        "skills",
        "mcp_servers",
        "enabled_plugins",
    }
    if set(options) != required:
        raise ValueError("options must contain the declared desktop settings")
    if options["permission_mode"] not in {"read_only", "ask", "full"}:
        raise ValueError("permission_mode must be read_only, ask, or full")
    if not isinstance(options["runtimes"], dict) or set(options["runtimes"]) != {"python", "node"}:
        raise ValueError("runtimes must contain python and node")
    if any(not isinstance(options["runtimes"][key], str) for key in options["runtimes"]):
        raise ValueError("runtime executable paths must be strings")
    if any(
        not isinstance(skill, dict)
        or set(skill) != {"id", "content", "source"}
        or any(not isinstance(skill[key], str) for key in skill)
        for skill in options["skills"]
    ):
        raise ValueError("skills must contain id, content, and source strings")
    if any(not isinstance(options[key], str) for key in ("model_code", "agent_content")):
        raise ValueError("model_code and agent_content must be strings")
    if not isinstance(options["max_context_tokens"], int):
        raise ValueError("max_context_tokens must be an integer")
    if options["max_context_tokens"] < 1:
        raise ValueError("max_context_tokens must be positive")
    if not isinstance(options["mcp_servers"], dict):
        raise ValueError("mcp_servers must be an object")
    runtime_dir = Path(params["runtime_data_dir"]).expanduser().resolve()
    values = dotenv_values(Path(__file__).parents[4] / "desktop" / ".env")
    base = HarnessSettings.model_validate(
        {
            "model": os.environ.get("VA_MODEL")
            or values.get("VA_MODEL")
            or HarnessSettings().model,
        }
    )
    model = options["model_code"] or base.model
    if ":" not in model and model.startswith("deepseek-"):
        model = f"deepseek:{model}"
    elif ":" not in model and model in {"deepseek", "openai", "anthropic", "google_genai"}:
        model = f"{model}:{model}"
    configured = HarnessSettings.model_validate(
        {
            **base.model_dump(),
            "model": model,
            "workspace_root": Path(params["workspace_root"]),
            "runtime_data_dir": runtime_dir,
            "store_db_path": runtime_dir / "store.sqlite",
            "checkpoint_db_path": runtime_dir / "checkpoints.sqlite",
            "mcp_servers": options["mcp_servers"],
            "enabled_plugins": options["enabled_plugins"],
        }
    )
    return configured, {**options}


def environment_defaults() -> dict[str, Any]:
    values = dotenv_values(Path(__file__).parents[4] / "desktop" / ".env")
    model = os.environ.get("VA_MODEL") or values.get("VA_MODEL") or HarnessSettings().model
    return {"model": model, "exa_configured": False, "analysis_enabled": False}


def runtime_environment(options: dict[str, Any] | None = None) -> dict[str, str | None]:
    selected = options or {"runtimes": {"python": "", "node": ""}}
    python = selected["runtimes"]["python"] or sys.executable
    node = selected["runtimes"]["node"] or shutil.which("node")
    node_version = (
        subprocess.run(
            [node, "--version"], capture_output=True, text=True, check=True
        ).stdout.strip()
        if node
        else None
    )
    python_version = subprocess.run(
        [python, "--version"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return {
        "python_executable": python,
        "node_executable": node,
        "python_version": python_version.removeprefix("Python "),
        "node_version": node_version,
        "platform": sys.platform,
    }


def attachment_message(
    workspace_root: Path, text: str, attachments: list[dict[str, str]]
) -> str | list[dict[str, Any]]:
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
    from vertical_harness.desktop_attachments import content_blocks

    return content_blocks(sources, text, root)


def raw_message(message: Any) -> dict[str, Any]:
    return message.model_dump(mode="json", exclude_none=True)


def usage_estimate(
    messages: list[Any],
    model: str,
    maximum: int = 128_000,
    options: dict[str, Any] | None = None,
    selected_skill_ids: frozenset[str] | None = None,
) -> dict[str, Any]:
    history_tokens = count_tokens_approximately(messages)
    options = options or {}
    selected_skills = [
        skill
        for skill in options.get("skills", [])
        if selected_skill_ids is None or skill["id"] in selected_skill_ids
    ]
    system_content = options.get("agent_content", "")
    skills_content = "\n".join(skill["content"] for skill in selected_skills)
    known = {
        "system_prompt": count_tokens_approximately([HumanMessage(content=system_content)]),
        "skills": count_tokens_approximately([HumanMessage(content=skills_content)]),
        "history": history_tokens,
    }
    used = sum(known.values())
    sections = {
        name: {
            "name": name,
            "tokens": value,
            "bytes": len(content.encode("utf-8")),
            "ratio_of_used": value / used if used else 0,
            "ratio_of_max": value / maximum,
            "description": description,
        }
        for name, value, content, description in (
            (
                "system_prompt",
                known["system_prompt"],
                system_content,
                "Configured system instructions estimate",
            ),
            ("skills", known["skills"], skills_content, "Selected skills estimate"),
            (
                "history",
                history_tokens,
                str(messages),
                "Provider usage metadata or content-length estimate",
            ),
        )
    }
    sections.update(
        {
            name: {
                "name": name,
                "tokens": None,
                "bytes": None,
                "ratio_of_used": None,
                "ratio_of_max": None,
                "description": "Not available from the current desktop context",
            }
            for name in ("mcp", "memory")
        }
    )
    return {
        "max_tokens": maximum,
        "used_tokens": used,
        "remaining_tokens": maximum - used,
        "used_ratio": used / maximum,
        "remaining_ratio": (maximum - used) / maximum,
        "sections": sections,
        "summary_text": f"Estimated {used:,} / {maximum:,} known tokens",
    }
