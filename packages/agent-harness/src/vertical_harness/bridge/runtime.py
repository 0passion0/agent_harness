"""桌面端运行时配置解析、环境探测与中间件编排模块。"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from vertical_harness.config.langsmith_settings import LangSmithSettings
from vertical_harness.config.settings import HarnessSettings
from vertical_harness.tools.search import WebSearch


class DesktopRuntime:
    """桌面端运行时配置工厂与执行环境探测器。"""

    @staticmethod
    def configured_settings(params: dict[str, Any]) -> HarnessSettings:
        """根据桌面端通过 JSON-RPC 传入的初始化参数构造并校验 HarnessSettings 与运行选项。"""
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
        if not isinstance(options["runtimes"], dict) or set(options["runtimes"]) != {
            "python",
            "node",
        }:
            raise ValueError("runtimes must contain python and node")
        if any(not isinstance(options["runtimes"][key], str) for key in options["runtimes"]):
            raise ValueError("runtime executable paths must be strings")
        if any(not isinstance(options[key], str) for key in ("model_code", "agent_content")):
            raise ValueError("model_code and agent_content must be strings")
        if type(options["max_context_tokens"]) is not int:
            raise ValueError("max_context_tokens must be an integer")
        if not 1 <= options["max_context_tokens"] <= 475_000:
            raise ValueError("max_context_tokens must be between 1 and 475000")
        runtime_dir = Path(params["runtime_data_dir"]).expanduser().resolve()
        values = dotenv_values(Path(__file__).parents[5] / "desktop" / ".env")
        base = HarnessSettings.model_validate(
            {
                "model": os.environ.get("VA_MODEL")
                or values.get("VA_MODEL")
                or HarnessSettings.model_validate({}).model,
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
                "node_executable": options["runtimes"]["node"],
                "permission_mode": options["permission_mode"],
                "max_context_tokens": options["max_context_tokens"],
                "agent_content": options["agent_content"],
            }
        )
        return configured

    @staticmethod
    def environment_defaults() -> dict[str, Any]:
        """读取桌面端默认环境配置（模型及 Exa 搜索是否配置）。"""
        values = dotenv_values(Path(__file__).parents[5] / "desktop" / ".env")
        model = (
            os.environ.get("VA_MODEL")
            or values.get("VA_MODEL")
            or HarnessSettings.model_validate({}).model
        )
        return {"model": model, "exa_configured": bool(WebSearch.search_api_key())}

    @staticmethod
    def runtime_environment(settings: HarnessSettings | None = None) -> dict[str, str | None]:
        """探测当前主机的 Python 与 Node 运行时执行环境及版本号。"""
        python = settings.python_executable if settings is not None else sys.executable
        node = (settings.node_executable if settings is not None else "") or shutil.which("node")
        node_version = (
            subprocess.run(
                [node, "--version"], capture_output=True, text=True, check=True, timeout=3
            ).stdout.strip()
            if node
            else None
        )
        python_version = subprocess.run(
            [python, "--version"],
            capture_output=True,
            text=True,
            check=True,
            timeout=3,
        ).stdout.strip()
        return {
            "python_executable": python,
            "node_executable": node,
            "python_version": python_version.removeprefix("Python "),
            "node_version": node_version,
            "platform": sys.platform,
        }

    @staticmethod
    def prepare_tracing_revision() -> None:
        if LangSmithSettings().tracing and (
            not (os.getenv("LANGSMITH_REVISION_ID") or os.getenv("LANGCHAIN_REVISION_ID"))
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
            except Exception:
                revision_id = "unknown"
            os.environ["LANGSMITH_REVISION_ID"] = revision_id
