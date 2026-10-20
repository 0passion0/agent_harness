from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from langchain.agents.middleware import AgentMiddleware, ModelRequest
from langchain_core.messages import ToolMessage
from langchain_core.messages.utils import count_tokens_approximately
from langchain_core.tools import tool
from langgraph.types import interrupt

from vertical_harness.config import HarnessSettings
from vertical_harness.search import search_api_key


class ContextLimitMiddleware(AgentMiddleware):
    """Reject requests whose full token estimate exceeds the configured limit."""

    after_summarization = True

    def __init__(self, maximum: int):
        self.maximum = maximum
        self.tools = []

    def _check(self, request: ModelRequest[Any]) -> None:
        messages = request.messages
        if request.system_message is not None:
            messages = [request.system_message, *messages]
        estimate = count_tokens_approximately(messages, tools=request.tools)
        if estimate > self.maximum:
            raise ValueError(
                f"Estimated conversation history ({estimate} tokens) exceeds configured "
                f"max_context_tokens ({self.maximum}); history was not sent to the model"
            )

    def wrap_model_call(self, request: ModelRequest[Any], handler: Any) -> Any:
        self._check(request)
        return handler(request)

    async def awrap_model_call(self, request: ModelRequest[Any], handler: Any) -> Any:
        self._check(request)
        return await handler(request)


@tool
def ask_user(question: str, options: list[str] | None = None) -> str:
    """Ask for missing information or a choice. Users may pick an option or write their own answer.

    This asks a question, never grants system permissions.
    """
    if not question.strip() or (
        options is not None and any(not option.strip() for option in options)
    ):
        raise ValueError("Questions and answer options must not be blank")
    answer = interrupt({"kind": "question", "question": question, "options": options or []})
    if not isinstance(answer, str) or not answer.strip():
        raise ValueError("User answer must be non-empty text")
    return answer


class DesktopPermissionMiddleware(AgentMiddleware):
    """Enforce read-only mode across every tool exposed by the composed agent."""

    _READ_ONLY_TOOLS = frozenset(
        {"read_file", "ls", "glob", "grep", "ask_user", "web_search", "write_todos"}
    )

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
        if name not in {
            "write_file",
            "edit_file",
            "delete",
            "execute",
            "execute_python_file",
            "write_todos",
        }:
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
        if (
            len(decision["decisions"]) != 1
            or not isinstance(decision["decisions"][0], dict)
            or decision["decisions"][0].get("type") not in {"approve", "reject"}
        ):
            raise ValueError("Expected exactly one approve or reject decision")
        if decision["decisions"][0]["type"] != "approve":
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


def configured_settings(params: dict[str, Any]) -> tuple[HarnessSettings, dict[str, Any]]:
    options = params["options"]
    required = {
        "model_code",
        "runtimes",
        "permission_mode",
        "max_context_tokens",
        "agent_content",
    }
    if not isinstance(options, dict) or set(options) != required:
        raise ValueError("options must contain the declared desktop settings")
    if options["permission_mode"] not in {"read_only", "ask", "full"}:
        raise ValueError("permission_mode must be read_only, ask, or full")
    if not isinstance(options["runtimes"], dict) or set(options["runtimes"]) != {"python", "node"}:
        raise ValueError("runtimes must contain python and node")
    if any(not isinstance(options["runtimes"][key], str) for key in options["runtimes"]):
        raise ValueError("runtime executable paths must be strings")
    if any(not isinstance(options[key], str) for key in ("model_code", "agent_content")):
        raise ValueError("model_code and agent_content must be strings")
    if not isinstance(options["max_context_tokens"], int):
        raise ValueError("max_context_tokens must be an integer")
    if not 1 <= options["max_context_tokens"] <= 475_000:
        raise ValueError("max_context_tokens must be between 1 and 475000")
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
            "python_executable": options["runtimes"]["python"] or sys.executable,
        }
    )
    return configured, {**options}


def environment_defaults() -> dict[str, Any]:
    values = dotenv_values(Path(__file__).parents[4] / "desktop" / ".env")
    model = os.environ.get("VA_MODEL") or values.get("VA_MODEL") or HarnessSettings().model
    return {"model": model, "exa_configured": bool(search_api_key())}


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
    *,
    request_messages: list[Any] | None = None,
    tools: list[Any] | None = None,
    reported_tokens: int | None = None,
) -> dict[str, Any]:
    captured = request_messages is not None
    groups: dict[str, list[Any]] = {name: [] for name in ("system_prompt", "tools", "history")}
    for message in request_messages if captured else messages:
        groups["system_prompt" if message.type == "system" else "history"].append(message)
    known = {
        name: count_tokens_approximately(items) if items else 0 for name, items in groups.items()
    }
    known["tools"] = count_tokens_approximately([], tools=tools) if tools else 0
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
    labels = {"system_prompt": "系统提示词", "tools": "工具", "history": "历史对话"}
    sections = {
        name: {
            "name": labels[name],
            "available": captured or name == "history",
            "tokens": value if captured or name == "history" else None,
            "bytes": None,
            "ratio_of_used": (value / used if used else 0)
            if captured or name == "history"
            else None,
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
