"""桌面端运行时配置解析、环境探测与中间件编排模块。"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from vertical_harness.models import HarnessSettings, LangSmithSettings
from vertical_harness.tools.search import WebSearch


class DesktopRuntime:
    """桌面端运行时配置工厂与执行环境探测器。"""

    @staticmethod
    def configured_settings(params: dict[str, Any]) -> HarnessSettings:
        """根据桌面端通过 JSON-RPC 传入的初始化参数构造并校验 HarnessSettings 与运行选项。

        Args:
            params: 包含配置初始化参数的字典，要求顶层必须包含以下键：
                - 'options' (dict): 包含以下配置项：
                    - 'model_code' (str): 目标模型编码。
                    - 'runtimes' (dict): 包含 'python' 与 'node' 可执行程序路径的字典。
                    - 'permission_mode' (str): 权限拦截模式 ('read_only', 'ask', 'full')。
                    - 'max_context_tokens' (int): 单次上下文 Token 预算上限 (1~475000)。
                    - 'agent_content' (str): 注入到系统提示词的业务自定义指令。
                - 'workspace_root' (str): 工作区本地物理根目录。
                - 'runtime_data_dir' (str): 运行时数据库隔离目录。

        Returns:
            HarnessSettings: 经过 Pydantic v2 严格验证的应用宿主设置对象。

        Raises:
            ValueError: 当 options 缺失必须字段、枚举值非法、路径非字符串或 Token 限制超出区间时抛出。
        """
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
        """读取桌面端默认环境配置（模型及 Exa 搜索是否配置）。

        Returns:
            dict[str, Any]: 包含默认环境状态的字典：
                - 'model' (str): 默认模型编码。
                - 'exa_configured' (bool): Exa 搜索 API 密钥是否已配置。
        """
        values = dotenv_values(Path(__file__).parents[5] / "desktop" / ".env")
        model = (
            os.environ.get("VA_MODEL")
            or values.get("VA_MODEL")
            or HarnessSettings.model_validate({}).model
        )
        return {"model": model, "exa_configured": bool(WebSearch.search_api_key())}

    @staticmethod
    def runtime_environment(settings: HarnessSettings | None = None) -> dict[str, str | None]:
        """探测当前主机的 Python 与 Node 运行时执行环境及版本号。

        Args:
            settings: 可选的应用宿主配置对象；为 None 时使用当前 Python 解释器与系统 PATH 中的 node。

        Returns:
            dict[str, str | None]: 包含当前主机运行环境诊断的字典：
                - 'python_executable' (str): Python 解释器绝对路径。
                - 'node_executable' (str | None): Node 解释器路径。
                - 'python_version' (str): Python 版本号字符串（如 '3.12.11'）。
                - 'node_version' (str | None): Node 版本号字符串（如 'v20.10.0'）。
                - 'platform' (str): 宿主操作系统平台标识（如 'win32', 'linux'）。
        """
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
        """尝试通过 git describe 获取代码仓库版本标识并注入 LANGSMITH_REVISION_ID。

        Returns:
            None
        """
        # 仅在开启链路追踪且未显式指定版本环境变量时探测 git 提交版本
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
                # 非 Git 目录或 git 命令不存在时静默降级为 unknown
                revision_id = "unknown"
            os.environ["LANGSMITH_REVISION_ID"] = revision_id
