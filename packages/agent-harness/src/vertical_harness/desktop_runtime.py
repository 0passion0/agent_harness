from __future__ import annotations

import os
import shutil
import subprocess
import sys
from contextlib import asynccontextmanager
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any

from deepagents.middleware import SkillsMiddleware
from dotenv import dotenv_values
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately
from langchain_core.tools import tool
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
from vertical_harness.engine.policy import ContextLimitMiddleware, PluginAuthorizationGateway


@tool
def ask_user(question: str, options: list[str] | None = None) -> str:
    """Ask for missing information or a choice. Users may pick an option or write their own answer.

    This asks a question, never grants plugin or system permissions.
    """
    if not question.strip() or (options is not None and any(not option.strip() for option in options)):
        raise ValueError("Questions and answer options must not be blank")
    answer = interrupt({"kind": "question", "question": question, "options": options or []})
    if not isinstance(answer, str) or not answer.strip():
        raise ValueError("User answer must be non-empty text")
    return answer


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
        selected_plugin_instructions = ""
        if self.selected_plugin_ids:
            capabilities = ", ".join(sorted(self.selected_plugin_ids))
            selected_plugin_instructions = (
                "Plugins selected for this user turn: "
                + capabilities
                + ". Use each plugin's registered tools or workflows according to its instructions."
            )
            static_content += "\n\n" + selected_plugin_instructions
        middleware: list[AgentMiddleware] = [
            ContextLimitMiddleware(self.options["max_context_tokens"], static_content, [ask_user]),
            DesktopPermissionMiddleware(self.options["permission_mode"]),
            RefreshSkillsMiddleware(),
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
                for part in (
                    self.options["agent_content"], selected_plugin_instructions,
                    "Use ask_user only for essential information that cannot be read from supplied files. "
                    "Native approval enforces the selected permission mode; execute clear requests without "
                    "repeated chat-only permission questions. Respect requests to run one method at a time. "
                    "File tools use virtual /paths; scripts use paths relative to the workspace. "
                    "Save complete code and deliverables; check exit_code, files and key outputs before "
                    "reporting success. Read saved scripts and logs when full contents are needed. "
                    "Reuse verified outputs unless inputs or methods change or a check fails. "
                    "After a rejected tool call, correct the cause or state the blocker. "
                    "Keep updates and conclusions concise, link verified deliverables and state limits. "
                    "Never invent data, results or successful execution.",
                )
                if part
            ),
        )


class RefreshSkillsMiddleware(AgentMiddleware):
    """Re-discover enabled skills each turn instead of reusing checkpointed catalogs."""

    state_schema = SkillsMiddleware.state_schema

    def before_agent(self, state: Any, runtime: Any) -> dict[str, Any]:
        return {"skills_metadata": None}


class DesktopPermissionMiddleware(AgentMiddleware):
    """Enforce read-only mode across every tool exposed by the composed agent."""

    _READ_ONLY_TOOLS = frozenset({"read_file", "ls", "glob", "grep", "ask_user"})

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
        if name not in {"write_file", "edit_file", "delete", "execute", "execute_python_file", "write_todos"}:
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
    if set(options) - {"authorized_plugins"} != required:
        raise ValueError("options must contain the declared desktop settings")
    if not isinstance(options.get("authorized_plugins", []), list) or any(
        not isinstance(plugin_id, str) for plugin_id in options.get("authorized_plugins", [])
    ):
        raise ValueError("authorized_plugins must be a list of plugin IDs")
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
    if not 1 <= options["max_context_tokens"] <= 475_000:
        raise ValueError("max_context_tokens must be between 1 and 475000")
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
            "python_executable": options["runtimes"]["python"] or sys.executable,
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
    maximum: int = 475_000,
    options: dict[str, Any] | None = None,
    selected_skill_ids: frozenset[str] | None = None,
    *,
    request_messages: list[Any] | None = None,
    tools: list[Any] | None = None,
    reported_tokens: int | None = None,
) -> dict[str, Any]:
    captured = request_messages is not None
    groups: dict[str, list[Any]] = {
        name: [] for name in ("system_prompt", "skills", "mcp", "memory", "history")
    }
    calls: dict[str, str] = {}
    for message in request_messages if captured else messages:
        if isinstance(message, SystemMessage):
            for block in message.content_blocks:
                content = block.get("text", "")
                section = "system_prompt"
                if "## Skills System" in content:
                    section = "skills"
                elif content.lstrip().startswith("User memory ("):
                    section = "memory"
                groups[section].append(SystemMessage(content=content))
        else:
            if isinstance(message, AIMessage):
                for call in message.tool_calls:
                    file_path = str(call.get("args", {}).get("file_path", ""))
                    section = "history"
                    if call["name"] == "read_file" and file_path.startswith("/skills/"):
                        section = "skills"
                    elif call["name"] == "recall_user_memory":
                        section = "memory"
                    calls[call["id"]] = section
            section = calls.get(message.tool_call_id, "history") if isinstance(message, ToolMessage) else "history"
            groups[section].append(message)
    known = {name: count_tokens_approximately(items) if items else 0 for name, items in groups.items()}
    known["mcp"] = count_tokens_approximately([], tools=tools) if tools else 0
    estimated = sum(known.values())
    # Providers report the total, not a per-section tokenizer breakdown.
    used = reported_tokens if reported_tokens is not None else estimated
    if reported_tokens is not None and estimated:
        cumulative = 0
        allocated = 0
        for name, value in known.items():
            cumulative += value
            boundary = round(cumulative * used / estimated)
            known[name], allocated = boundary - allocated, boundary
    labels = {
        "system_prompt": "系统提示词", "skills": "技能 (Skills)",
        "mcp": "MCP 及工具", "memory": "记忆", "history": "历史对话",
    }
    sections = {
        name: {
            "name": labels[name],
            "available": captured or name == "history",
            "tokens": value if captured or name == "history" else None,
            "bytes": None,
            "ratio_of_used": (value / used if used else 0) if captured or name == "history" else None,
            "ratio_of_max": value / maximum if captured or name == "history" else None,
            "description": "按实际请求内容估算构成" if captured else "尚未采集实际模型请求",
        }
        for name, value in known.items()
    }
    remaining = max(0, maximum - used)
    summary = "尚未采集实际模型请求；当前仅估算历史消息，其他区块未统计。"
    if reported_tokens is not None:
        summary = "最近一次主 Agent 请求及其输出；总量由模型上报，各项按内容比例估算。"
    elif captured:
        summary = "最近一次主 Agent 实际请求及其输出的内容估算；模型未返回用量。"
    return {
        "max_tokens": maximum,
        "used_tokens": used,
        "remaining_tokens": remaining,
        "used_ratio": used / maximum,
        "remaining_ratio": remaining / maximum,
        "sections": sections,
        "summary_text": summary,
    }
