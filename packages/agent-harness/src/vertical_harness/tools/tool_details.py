"""异步捕获并收集工具与模型调用的入参、耗时及实际 Token 请求的观察处理器模块。"""

from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter
from typing import Any
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler

from vertical_harness.utils.usage import UsageEstimator


@dataclass(eq=False)
class ToolDetails(AsyncCallbackHandler):
    """异步捕获并收集工具与模型调用的入参、耗时及实际 Token 请求的观察处理器。

    :author: tongliga
    :date: 2026-10-21
    """

    run_inline = True

    maximum: int = 475_000
    records: dict[str, dict[str, Any]] = field(default_factory=dict, init=False)
    started: dict[str, float] = field(default_factory=dict, init=False)
    context_requests: dict[str, tuple[list[Any], list[Any]]] = field(default_factory=dict, init=False)
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

        :param serialized: 序列化模型元数据。
        :type serialized: dict[str, typing.Any]
        :param messages: 模型输入消息列表。
        :type messages: list[typing.Any]
        :param run_id: 操作运行 ID。
        :type run_id: uuid.UUID
        :param metadata: 调用元数据字典。
        :type metadata: dict[str, typing.Any] | None
        :param invocation_params: 调用参数。
        :type invocation_params: dict[str, typing.Any] | None
        :param kwargs: 其他关键字参数。
        :type kwargs: typing.Any
        :return: 无返回值。
        :rtype: None
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：校验当前调用是否来自主智能体且包含输入消息
        if (metadata or {}).get("lc_agent_name") == "vertical-agent" and messages:
            # 第二步：将首条请求消息与绑定的工具描述存入 context_requests
            self.context_requests[str(run_id)] = (
                messages[0],
                (invocation_params or {}).get("tools", []),
            )

    async def on_llm_end(self, response: Any, *, run_id: UUID, **kwargs: Any) -> None:
        """模型调用完成时提取真实用量并估算各分区 Token 消耗。

        :param response: 模型响应对象。
        :type response: typing.Any
        :param run_id: 操作运行 ID。
        :type run_id: uuid.UUID
        :param kwargs: 其他关键字参数。
        :type kwargs: typing.Any
        :return: 无返回值。
        :rtype: None
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：检索该 run_id 在起始阶段保存的请求参数
        request = self.context_requests.pop(str(run_id), None)
        if request is None:
            return
        messages, tools = request

        # 第二步：从模型生成响应中提取首条消息与用量元数据
        message = response.generations[0][0].message
        usage = message.usage_metadata

        # 第三步：调用用量估算器计算完整分区报告并持久化到内存缓存
        self.context_reports[str(run_id)] = UsageEstimator.usage_estimate(
            [],
            "",
            self.maximum,
            request_messages=[*messages, message],
            tools=tools,
            reported_tokens=usage["input_tokens"] + usage["output_tokens"] if usage else None,
        )

    async def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        """模型调用异常时清理对应的上下文追踪请求缓存。

        :param error: 异常对象。
        :type error: BaseException
        :param run_id: 操作运行 ID。
        :type run_id: uuid.UUID
        :param kwargs: 其他关键字参数。
        :type kwargs: typing.Any
        :return: 无返回值。
        :rtype: None
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：异常时安全弹出并清理请求缓存
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

        :param serialized: 序列化工具元数据。
        :type serialized: dict[str, typing.Any]
        :param input_str: 工具输入字符串。
        :type input_str: str
        :param run_id: 操作运行 ID。
        :type run_id: uuid.UUID
        :param inputs: 工具输入参数字典。
        :type inputs: dict[str, typing.Any] | None
        :param kwargs: 其他关键字参数。
        :type kwargs: typing.Any
        :return: 无返回值。
        :rtype: None
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：记录起始高精度时间戳与输入参数
        key = str(run_id)
        self.started[key] = perf_counter()
        self.records[key] = {"arguments": inputs}

    async def on_tool_end(self, output: Any, *, run_id: UUID, **kwargs: Any) -> None:
        """记录工具成功执行完成的输出内容与持续时间。

        :param output: 工具输出结果对象。
        :type output: typing.Any
        :param run_id: 操作运行 ID。
        :type run_id: uuid.UUID
        :param kwargs: 其他关键字参数。
        :type kwargs: typing.Any
        :return: 无返回值。
        :rtype: None
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：计算耗时并记录输出正文
        key = str(run_id)
        self.records[key].update(
            {
                "output": output.content,
                "elapsed": perf_counter() - self.started[key],
            }
        )

    async def on_tool_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        """记录工具执行发生异常时的错误类型与耗时。

        :param error: 异常对象。
        :type error: BaseException
        :param run_id: 操作运行 ID。
        :type run_id: uuid.UUID
        :param kwargs: 其他关键字参数。
        :type kwargs: typing.Any
        :return: 无返回值。
        :rtype: None
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：计算耗时并记录异常类型
        key = str(run_id)
        self.records[key].update(
            {
                "output": {"error_type": type(error).__name__},
                "elapsed": perf_counter() - self.started[key],
            }
        )


__all__ = ["ToolDetails"]
