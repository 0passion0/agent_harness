"""桌面应用运行时支持模块：包含上下文阈值校验、安全权限中间件、人机交互中断及资源用量估算。"""

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
    """拦截并拒绝上下文 Token 估算超出配置上限阈值的模型调用请求中间件。

    :author: tongliga
    :date: 2026-10-21
    """

    after_summarization = True

    def __init__(self, maximum: int) -> None:
        """初始化上下文限制中间件。

        :param maximum: 允许进入模型的上下文 Token 最大阈值。
        :type maximum: int
        :return: 无返回值。
        :rtype: None
        :author: tongliga
        :date: 2026-10-21
        """
        self.maximum = maximum
        self.tools: list[Any] = []

    def _check(self, request: ModelRequest[Any]) -> None:
        """估算请求包含的所有消息与工具描述 Token 总量，超限则抛出异常。

        :param request: 模型请求对象。
        :type request: langchain.agents.middleware.ModelRequest[typing.Any]
        :return: 无返回值。
        :rtype: None
        :raises ValueError: 当 Token 估算值超出配置的 maximum 阈值时抛出。
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：合并系统提示词与历史消息列表
        messages = request.messages
        if request.system_message is not None:
            messages = [request.system_message, *messages]

        # 第二步：调用 LangChain 工具函数近似计算 Token 数量
        estimate = count_tokens_approximately(messages, tools=request.tools)

        # 第三步：超限断言拦截，防止请求发送至模型服务商
        if estimate > self.maximum:
            raise ValueError(
                f"Estimated conversation history ({estimate} tokens) exceeds configured "
                f"max_context_tokens ({self.maximum}); history was not sent to the model"
            )

    def wrap_model_call(self, request: ModelRequest[Any], handler: Any) -> Any:
        """同步拦截模型调用请求并执行容量检查。

        :param request: 模型请求对象。
        :type request: langchain.agents.middleware.ModelRequest[typing.Any]
        :param handler: 下游调用处理器函数。
        :type handler: typing.Any
        :return: 模型响应结果。
        :rtype: typing.Any
        :author: tongliga
        :date: 2026-10-21
        """
        self._check(request)
        return handler(request)

    async def awrap_model_call(self, request: ModelRequest[Any], handler: Any) -> Any:
        """异步拦截模型调用请求并执行容量检查。

        :param request: 模型请求对象。
        :type request: langchain.agents.middleware.ModelRequest[typing.Any]
        :param handler: 下游异步调用处理器函数。
        :type handler: typing.Any
        :return: 模型响应结果。
        :rtype: typing.Any
        :author: tongliga
        :date: 2026-10-21
        """
        self._check(request)
        return await handler(request)


@tool
def ask_user(question: str, options: list[str] | None = None) -> str:
    """Ask for missing information or a choice. Users may pick an option or write their own answer.

    This asks a question, never grants system permissions.

    :param question: 向用户提出的清晰、具体的疑问或缺失信息说明。
    :type question: str
    :param options: 可选的候选项列表，供用户单选或填写自定义回复。
    :type options: list[str] | None
    :return: 用户提交的答案或选择文本。
    :rtype: str
    :raises ValueError: 当问题为空或候选项包含空字符串，或用户回复为空时抛出。
    :author: tongliga
    :date: 2026-10-21
    """
    # 第一步：校验提问文本及候选项非空
    if not question.strip() or (
        options is not None and any(not option.strip() for option in options)
    ):
        raise ValueError("Questions and answer options must not be blank")

    # 第二步：调用 LangGraph 原生 interrupt 触发人机交互挂起，等待桌面端恢复
    answer = interrupt({"kind": "question", "question": question, "options": options or []})

    # 第三步：校验用户恢复传入的答案非空并返回
    if not isinstance(answer, str) or not answer.strip():
        raise ValueError("User answer must be non-empty text")
    return answer


class DesktopPermissionMiddleware(AgentMiddleware):
    """在只读模式（read_only）下强制禁用所有具副作用工具的权限中间件。

    :author: tongliga
    :date: 2026-10-21
    """

    _READ_ONLY_TOOLS = frozenset(
        {"read_file", "ls", "glob", "grep", "ask_user", "web_search", "write_todos"}
    )

    def __init__(self, mode: str) -> None:
        """初始化桌面权限控制中间件。

        :param mode: 运行权限模式，如 'read_only', 'ask', 'full'。
        :type mode: str
        :return: 无返回值。
        :rtype: None
        :author: tongliga
        :date: 2026-10-21
        """
        self.mode = mode
        self.tools: list[Any] = []

    def wrap_tool_call(self, request: Any, handler: Any) -> Any:
        """同步拦截工具调用，若在只读模式下尝试执行白名单外工具则抛出权限错误。

        :param request: 工具调用请求对象。
        :type request: typing.Any
        :param handler: 下游工具调用处理器。
        :type handler: typing.Any
        :return: 工具调用结果。
        :rtype: typing.Any
        :raises PermissionError: 当只读模式下调用非只读工具时抛出。
        :author: tongliga
        :date: 2026-10-21
        """
        if self.mode == "read_only" and request.tool.name not in self._READ_ONLY_TOOLS:
            raise PermissionError(f"Tool {request.tool.name} is disabled in read_only mode")
        return handler(request)

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        """异步拦截工具调用，若在只读模式下尝试执行白名单外工具则抛出权限错误。

        :param request: 工具调用请求对象。
        :type request: typing.Any
        :param handler: 下游异步工具调用处理器。
        :type handler: typing.Any
        :return: 工具调用结果。
        :rtype: typing.Any
        :raises PermissionError: 当只读模式下调用非只读工具时抛出。
        :author: tongliga
        :date: 2026-10-21
        """
        if self.mode == "read_only" and request.tool.name not in self._READ_ONLY_TOOLS:
            raise PermissionError(f"Tool {request.tool.name} is disabled in read_only mode")
        return await handler(request)


class DesktopApprovalMiddleware(AgentMiddleware):
    """在执行文件修改或外部命令等副作用工具前触发 LangGraph 人机审批中断的中间件。

    :author: tongliga
    :date: 2026-10-21
    """

    def __init__(self) -> None:
        """初始化人机审批中间件。

        :return: 无返回值。
        :rtype: None
        :author: tongliga
        :date: 2026-10-21
        """
        self.tools: list[Any] = []

    @staticmethod
    def _approve(request: Any) -> ToolMessage | None:
        """检查工具是否需要审批，需要时发起 interrupt 并在拒绝时返回错误 ToolMessage。

        :param request: 工具请求对象。
        :type request: typing.Any
        :return: 若用户拒绝则返回包含拒绝说明的 ToolMessage；若同意或无需审批则返回 None。
        :rtype: langchain_core.messages.ToolMessage | None
        :raises ValueError: 当审批应答格式不合法时抛出。
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：判断工具是否属于需要审批的高风险操作集
        name = request.tool.name
        if name not in {
            "write_file",
            "edit_file",
            "delete",
            "execute",
            "execute_python_file",
            "write_todos",
        }:
            return None

        # 第二步：挂起当前执行流，向桌面端发出 approval 审批请求
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

        # 第三步：校验审批响应结构必须为单个 approve 或 reject 决策
        if not isinstance(decision, dict) or not isinstance(decision.get("decisions"), list):
            raise ValueError("Approval response must contain decisions")
        if (
            len(decision["decisions"]) != 1
            or not isinstance(decision["decisions"][0], dict)
            or decision["decisions"][0].get("type") not in {"approve", "reject"}
        ):
            raise ValueError("Expected exactly one approve or reject decision")

        # 第四步：若用户拒绝，则生成对应的错误状态 ToolMessage 中断执行
        if decision["decisions"][0]["type"] != "approve":
            return ToolMessage(
                content="Action rejected by user; the tool was not executed.",
                name=name,
                tool_call_id=request.tool_call["id"],
                status="error",
            )
        return None

    def wrap_tool_call(self, request: Any, handler: Any) -> Any:
        """同步拦截工具调用并执行人机审批流程。

        :param request: 工具请求对象。
        :type request: typing.Any
        :param handler: 下游工具调用处理器。
        :type handler: typing.Any
        :return: 工具调用结果或被拒绝时的 ToolMessage。
        :rtype: typing.Any
        :author: tongliga
        :date: 2026-10-21
        """
        rejected = self._approve(request)
        if rejected is not None:
            return rejected
        return handler(request)

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        """异步拦截工具调用并执行人机审批流程。

        :param request: 工具请求对象。
        :type request: typing.Any
        :param handler: 下游异步工具调用处理器。
        :type handler: typing.Any
        :return: 工具调用结果或被拒绝时的 ToolMessage。
        :rtype: typing.Any
        :author: tongliga
        :date: 2026-10-21
        """
        rejected = self._approve(request)
        if rejected is not None:
            return rejected
        return await handler(request)


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
    values = dotenv_values(Path(__file__).parents[4] / "desktop" / ".env")
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


def environment_defaults() -> dict[str, Any]:
    """读取桌面端默认环境配置（模型及 Exa 搜索是否配置）。

    :return: 包含 model 与 exa_configured 的字典。
    :rtype: dict[str, typing.Any]
    :author: tongliga
    :date: 2026-10-21
    """
    # 第一步：读取 desktop/.env 中的默认配置
    values = dotenv_values(Path(__file__).parents[4] / "desktop" / ".env")
    model = os.environ.get("VA_MODEL") or values.get("VA_MODEL") or HarnessSettings().model

    # 第二步：检查 Exa 搜索密钥配置状态并返回
    return {"model": model, "exa_configured": bool(search_api_key())}


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


def attachment_message(
    workspace_root: Path, text: str, attachments: list[dict[str, str]]
) -> str | list[dict[str, Any]]:
    """校验附件安全性（必须在工作区物理边界内）并生成多模态消息内容块。

    :param workspace_root: 当前工作区的物理根目录 Path 对象。
    :type workspace_root: pathlib.Path
    :param text: 用户输入的原始提示词。
    :type text: str
    :param attachments: 附件字典列表，每项必须包含 name、path 与 media_type 字段。
    :type attachments: list[dict[str, str]]
    :return: 原样文本字符串或多模态消息块列表。
    :rtype: str | list[dict[str, typing.Any]]
    :raises ValueError: 当附件缺少必填字段、路径非绝对、文件不存在或超出工作区时抛出。
    :author: tongliga
    :date: 2026-10-21
    """
    # 第一步：解析工作区真实绝对路径
    root = workspace_root.expanduser().resolve()
    validated = []

    # 第二步：逐一校验附件元数据结构及文件存在性
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

    # 第三步：校验所有附件必须严格位于工作区目录之下
    sources = []
    for attachment, source in validated:
        try:
            source.relative_to(root)
        except ValueError as exc:
            raise ValueError("attachment must be inside the workspace") from exc
        sources.append((attachment, source))

    # 第四步：调用 content_blocks 生成标准多模态内容块
    from vertical_harness.desktop_attachments import content_blocks

    return content_blocks(sources, text, root)


def raw_message(message: Any) -> dict[str, Any]:
    """将 LangChain 消息对象导出为排除空字段的标准 JSON 字典。

    :param message: LangChain 消息对象（如 HumanMessage、AIMessage 等）。
    :type message: typing.Any
    :return: 序列化后的消息字典。
    :rtype: dict[str, typing.Any]
    :author: tongliga
    :date: 2026-10-21
    """
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
    """估算或按模型真实上报用量拆分各区块（系统提示词、工具定义、历史消息）的 Token 构成与占比。

    :param messages: 原始对话消息列表。
    :type messages: list[typing.Any]
    :param model: 模型标识符字符串。
    :type model: str
    :param maximum: 上下文预算上限，默认 475,000。
    :type maximum: int
    :param options: 可选的桌面配置选项。
    :type options: dict[str, typing.Any] | None
    :param request_messages: 实际发送给模型的完整请求消息列表（包含系统消息）。
    :type request_messages: list[typing.Any] | None
    :param tools: 绑定至模型的工具列表。
    :type tools: list[typing.Any] | None
    :param reported_tokens: 模型 API 返回的真实总 Token 消耗量。
    :type reported_tokens: int | None
    :return: 包含 max_tokens、used_tokens、remaining_tokens、sections 及 summary_text 的字典。
    :rtype: dict[str, typing.Any]
    :author: tongliga
    :date: 2026-10-21

    .. code-block:: python

        # return 结构契约：
        {
            "max_tokens": 475000,
            "used_tokens": 1250,
            "remaining_tokens": 473750,
            "used_ratio": 0.0026,
            "remaining_ratio": 0.9974,
            "sections": {
                "system_prompt": {"name": "系统提示词", "tokens": 400, ...},
                "tools": {"name": "工具", "tokens": 600, ...},
                "history": {"name": "历史对话", "tokens": 250, ...}
            },
            "summary_text": "..."
        }
    """
    # 第一步：根据是否捕获实际请求消息将消息分组为 system_prompt 与 history
    captured = request_messages is not None
    groups: dict[str, list[Any]] = {name: [] for name in ("system_prompt", "tools", "history")}
    for message in request_messages if captured else messages:
        groups["system_prompt" if message.type == "system" else "history"].append(message)

    # 第二步：基于分组消息与工具计算各区块的近似 Token 估算值
    known = {
        name: count_tokens_approximately(items) if items else 0 for name, items in groups.items()
    }
    known["tools"] = count_tokens_approximately([], tools=tools) if tools else 0
    estimated = sum(known.values())

    # 第三步：若模型返回了权威用量指标，按各区块估算比例进行等比归一化分配
    used = reported_tokens if reported_tokens is not None else estimated
    if reported_tokens is not None and estimated:
        cumulative = 0
        allocated = 0
        for name, value in known.items():
            cumulative += value
            boundary = round(cumulative * used / estimated)
            known[name], allocated = boundary - allocated, boundary

    # 第四步：格式化输出 sections 各分块统计明细
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

    # 第五步：生成摘要描述文本并返回统计结果
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
