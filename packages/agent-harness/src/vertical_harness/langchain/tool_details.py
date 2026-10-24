"""异步捕获并收集工具与模型调用的入参、耗时及实际 Token 请求的观察处理器模块。

本模块提供 `ToolDetails` 异步回调处理器，继承自 `AsyncCallbackHandler`：
1. 工具调用生命周期追踪：自动在工具启动时捕获入参字典（`inputs`），在完成或异常时捕获输出内容（`output`）并精确计算耗时（`elapsed`）；
2. 状态图命令解构：支持解构 LangGraph 的 `Command` 对象，从中解构出最新的消息输出；
3. 上下文用量精细化估算：拦截模型请求消息与挂载工具列表，结合响应消息的 `usage_metadata`，通过 `UsageEstimator` 生成上下文 Token 分区报告。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter
from typing import Any
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler
from langgraph.types import Command

from vertical_harness.utils.usage import UsageEstimator


@dataclass(eq=False)
class ToolDetails(AsyncCallbackHandler):
    """异步捕获并收集工具与模型调用的入参、耗时及实际 Token 请求的观察处理器。

    Attributes:
        maximum (int): 上下文 Token 估算上限阈值，默认 475,000。
        run_inline (bool): 标识回调处理器在当前执行线程中内联执行，恒为 True。
        records (dict[str, dict[str, Any]]): 工具调用执行明细记录字典（以 operation_id 字符串为键，包含入参、输出与耗时）。
        started (dict[str, float]): 工具调用启动时刻的高精度单调时间戳映射。
        context_requests (dict[str, tuple[list[Any], list[Any]]]): 模型调用发起时的输入消息与工具列表快照缓存。
        context_reports (dict[str, dict[str, Any]]): 每次模型调用完成后的 Token 区块用量分析报告。
    """

    # 上下文 Token 估算上限阈值
    maximum: int = 475_000
    # 标识回调处理器内联同步执行，覆盖基类默认行为
    run_inline: bool = field(default=True, init=False)
    # 工具调用执行明细记录字典（按 operation_id 存储入参、输出与耗时）
    records: dict[str, dict[str, Any]] = field(default_factory=dict, init=False)
    # 工具调用启动时刻的单调时间戳映射
    started: dict[str, float] = field(default_factory=dict, init=False)
    # 模型调用发起时的输入消息与工具列表快照缓存
    context_requests: dict[str, tuple[list[Any], list[Any]]] = field(
        default_factory=dict, init=False
    )
    # 每次模型调用完成后的 Token 区块用量分析报告
    context_reports: dict[str, dict[str, Any]] = field(default_factory=dict, init=False)

    async def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[Any],
        *,
        run_id: UUID,
        metadata: dict[str, Any] | None = None,
        invocation_params: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """记录模型发起调用时的第一层输入消息与工具列表。

        当识别到属于主智能体 `vertical-agent` 且消息列表非空时，暂存消息快照以供完成时分析。

        Args:
            serialized (dict[str, Any]): 聊天模型序列化元数据。
            messages (list[Any]): 发送给模型的输入消息批次。
            run_id (UUID): 本次模型调用的唯一执行 UUID。
            metadata (dict[str, Any] | None): 随图传递的调用元数据字典。
            invocation_params (dict[str, Any] | None): 模型调用参数，包含挂载的工具列表 `tools`。
            **kwargs (Any): 其他额外上下文参数。
        """
        if (metadata or {}).get("lc_agent_name") == "vertical-agent" and messages:
            self.context_requests[str(run_id)] = (
                messages[0],
                (invocation_params or {}).get("tools", []),
            )

    async def on_llm_end(self, response: Any, *, run_id: UUID, **kwargs: Any) -> None:
        """模型调用完成时提取真实用量并估算各分区 Token 消耗。

        结合请求消息与响应消息，计算系统指令、历史会话与工具定义各自占用的 Token 预算比例。

        Args:
            response (Any): 包含生成文本与 `usage_metadata` 的模型响应实体。
            run_id (UUID): 模型调用执行 UUID。
            **kwargs (Any): 附加元数据。
        """
        request = self.context_requests.pop(str(run_id), None)
        if request is None:
            return
        messages, tools = request
        message = response.generations[0][0].message
        usage = message.usage_metadata
        self.context_reports[str(run_id)] = UsageEstimator.usage_estimate(
            [],
            self.maximum,
            request_messages=[*messages, message],
            tools=tools,
            reported_tokens=usage["input_tokens"] + usage["output_tokens"] if usage else None,
        )

    async def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        """模型调用异常时清理对应的上下文追踪请求缓存。

        Args:
            error (BaseException): 捕获到的模型调用异常。
            run_id (UUID): 模型调用运行 ID。
            **kwargs (Any): 附加元数据。
        """
        self.context_requests.pop(str(run_id), None)

    async def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        inputs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        """记录工具开始执行的高精度时间与输入参数。

        Args:
            serialized (dict[str, Any]): 工具配置元数据。
            input_str (str): 工具调用原始字符串。
            run_id (UUID): 工具调用的唯一操作 ID。
            inputs (dict[str, Any] | None): 结构化传入的参数字典。
            **kwargs (Any): 附加参数。
        """
        key = str(run_id)
        self.started[key] = perf_counter()
        self.records[key] = {"arguments": inputs}

    async def on_tool_end(self, output: Any, *, run_id: UUID, **kwargs: Any) -> None:
        """记录工具成功执行完成的输出内容与持续时间。

        解构可能的 `Command` 对象，捕获纯净输出文本或 JSON 序列化对象并记录总执行秒数。

        Args:
            output (Any): 工具返回结果对象或 Command。
            run_id (UUID): 工具调用唯一操作 ID。
            **kwargs (Any): 附加参数。

        Raises:
            ValueError: 若传入的 Command 未包含有效消息时抛出。
        """
        key = str(run_id)
        if isinstance(output, Command):
            if not isinstance(output.update, dict) or not output.update.get("messages"):
                raise ValueError("Tool command must include messages")
            message = output.update["messages"][-1]
        else:
            message = output
        out = getattr(message, "content", message)
        self.records[key].update(
            {
                "output": out
                if isinstance(out, (str, int, float, bool, list, dict, type(None)))
                else str(out),
                "elapsed": perf_counter() - self.started.get(key, perf_counter()),
            }
        )

    async def on_tool_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        """记录工具执行发生异常时的错误类型与耗时。

        Args:
            error (BaseException): 捕获到的底层异常对象。
            run_id (UUID): 工具调用唯一操作 ID。
            **kwargs (Any): 附加参数。
        """
        key = str(run_id)
        self.records[key].update(
            {
                "output": {"error_type": type(error).__name__},
                "elapsed": perf_counter() - self.started.get(key, perf_counter()),
            }
        )
