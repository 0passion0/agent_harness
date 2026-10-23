"""异步捕获并收集工具与模型调用的入参、耗时及实际 Token 请求的观察处理器模块。"""

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
    """异步捕获并收集工具与模型调用的入参、耗时及实际 Token 请求的观察处理器。"""

    run_inline = True

    maximum: int = 475_000
    records: dict[str, dict[str, Any]] = field(default_factory=dict, init=False)
    started: dict[str, float] = field(default_factory=dict, init=False)
    context_requests: dict[str, tuple[list[Any], list[Any]]] = field(
        default_factory=dict, init=False
    )
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
        """记录模型发起调用时的第一层输入消息与工具列表。"""
        if (metadata or {}).get("lc_agent_name") == "vertical-agent" and messages:
            self.context_requests[str(run_id)] = (
                messages[0],
                (invocation_params or {}).get("tools", []),
            )

    async def on_llm_end(self, response: Any, *, run_id: UUID, **kwargs: Any) -> None:
        """模型调用完成时提取真实用量并估算各分区 Token 消耗。"""
        request = self.context_requests.pop(str(run_id), None)
        if request is None:
            return
        messages, tools = request
        message = response.generations[0][0].message
        usage = message.usage_metadata
        self.context_reports[str(run_id)] = UsageEstimator.usage_estimate(
            [],
            "",
            self.maximum,
            request_messages=[*messages, message],
            tools=tools,
            reported_tokens=usage["input_tokens"] + usage["output_tokens"] if usage else None,
        )

    async def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        """模型调用异常时清理对应的上下文追踪请求缓存。"""
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
        """记录工具开始执行的高精度时间与输入参数。"""
        key = str(run_id)
        self.started[key] = perf_counter()
        self.records[key] = {"arguments": inputs}

    async def on_tool_end(self, output: Any, *, run_id: UUID, **kwargs: Any) -> None:
        """记录工具成功执行完成的输出内容与持续时间。"""
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
        """记录工具执行发生异常时的错误类型与耗时。"""
        key = str(run_id)
        self.records[key].update(
            {
                "output": {"error_type": type(error).__name__},
                "elapsed": perf_counter() - self.started.get(key, perf_counter()),
            }
        )
