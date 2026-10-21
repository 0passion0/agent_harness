"""桌面端运行时配置解析、环境探测与中间件编排模块。"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from vertical_harness.config.settings import HarnessSettings
from vertical_harness.tools.search import WebSearch


class DesktopRuntime:
    """桌面端运行时配置工厂与执行环境探测器。

    :author: tongliga
    :date: 2026-10-21
    """

    @staticmethod
    def configured_settings(params: dict[str, Any]) -> tuple[HarnessSettings, dict[str, Any]]:
        """根据桌面端通过 JSON-RPC 传入的初始化参数构造并校验 HarnessSettings 与运行选项。

        :param params: 桌面端传入的参数字典，包含 workspace_root、runtime_data_dir 与 options。
        :type params: dict[str, typing.Any]
        :return: 包含初始化完成的 HarnessSettings 实例与校验后的选项字典的元组。
        :rtype: tuple[vertical_harness.config.settings.HarnessSettings, dict[str, typing.Any]]
        :raises ValueError: 当参数结构缺失、取值范围不合法时抛出。
        :author: tongliga
        :date: 2026-10-21

        .. code-block:: python

            # params 数据结构契约：
            {
                "tenant_id": "default",
                "user_id": "default",
                "workspace_root": "D:/workspace",
                "runtime_data_dir": "D:/workspace/.runtime",
                "options": {
                    "model_code": "deepseek:deepseek-chat",
                    "runtimes": {"python": "C:/python/python.exe", "node": ""},
                    "permission_mode": "full",
                    "max_context_tokens": 475000,
                    "agent_content": ""
                }
            }
        """
        # 第一步：校验 options 必填字段与合法取值
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

        # 第二步：解析运行数据目录与默认环境变量
        runtime_dir = Path(params["runtime_data_dir"]).expanduser().resolve()
        values = dotenv_values(Path(__file__).parents[5] / "desktop" / ".env")
        base = HarnessSettings.model_validate(
            {
                "model": os.environ.get("VA_MODEL")
                         or values.get("VA_MODEL")
                         or HarnessSettings().model,
            }
        )

        # 第三步：规格化模型标识符前缀（确保符合 provider:model 命名规范）
        model = options["model_code"] or base.model
        if ":" not in model and model.startswith("deepseek-"):
            model = f"deepseek:{model}"
        elif ":" not in model and model in {"deepseek", "openai", "anthropic", "google_genai"}:
            model = f"{model}:{model}"

        # 第四步：构造并验证最终 HarnessSettings 实例
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

    @staticmethod
    def environment_defaults() -> dict[str, Any]:
        """读取桌面端默认环境配置（模型及 Exa 搜索是否配置）。

        :return: 包含 model 与 exa_configured 的字典。
        :rtype: dict[str, typing.Any]
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：读取 desktop/.env 中的默认配置
        values = dotenv_values(Path(__file__).parents[5] / "desktop" / ".env")
        model = os.environ.get("VA_MODEL") or values.get("VA_MODEL") or HarnessSettings().model

        # 第二步：检查 Exa 搜索密钥配置状态并返回
        return {"model": model, "exa_configured": bool(WebSearch.search_api_key())}

    @staticmethod
    def runtime_environment(options: dict[str, Any] | None = None) -> dict[str, str | None]:
        """探测当前主机的 Python 与 Node 运行时执行环境及版本号。

        :param options: 可选的包含 runtimes 自定义解释器路径的字典。
        :type options: dict[str, typing.Any] | None
        :return: 包含 python_executable、node_executable、python_version、node_version 与 platform 的字典。
        :rtype: dict[str, str | None]
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：确定 Python 与 Node 解释器路径
        selected = options or {"runtimes": {"python": "", "node": ""}}
        python = selected["runtimes"]["python"] or sys.executable
        node = selected["runtimes"]["node"] or shutil.which("node")

        # 第二步：调用子进程获取解释器版本信息
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

        # 第三步：组装并返回运行环境元数据字典
        return {
            "python_executable": python,
            "node_executable": node,
            "python_version": python_version.removeprefix("Python "),
            "node_version": node_version,
            "platform": sys.platform,
        }
