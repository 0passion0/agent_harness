"""通过原生 LangChain callbacks 观察完整执行树，不耦合智能体或插件实现。"""

from __future__ import annotations

import logging
from asyncio import CancelledError
from copy import deepcopy
from threading import Lock
from time import perf_counter
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages.ai import UsageMetadata, add_usage
from langgraph.errors import GraphBubbleUp

logger = logging.getLogger(__name__)


class WorkflowLoggingHandler(BaseCallbackHandler):
    """记录链、模型、工具和子调用的阶段与耗时，不读取输入、输出或 token 内容。

    每次运行创建独立实例，通过 RunnableConfig.callbacks 注入；同步和异步通用。
    """

    run_inline = True

    def __init__(self) -> None:
        """初始化工作流执行树日志观察处理器。

        :return: 无返回值。
        :rtype: None
        :author: tongliga
        :date: 2026-10-19
        """
        super().__init__()
        self._started: dict[UUID, float] = {}
        self._lock = Lock()
        self._model_calls = 0
        self._usage_reported_calls = 0
        self._usage: UsageMetadata = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    @property
    def usage(self) -> dict[str, Any]:
        """汇总整个执行树的 Token 消耗量与完整性指标。

        :return: 包含 input_tokens、output_tokens、total_tokens、model_calls、usage_reported_calls 与 complete 的字典。
        :rtype: dict[str, typing.Any]
        :author: tongliga
        :date: 2026-10-19
        """
        with self._lock:
            return {
                **deepcopy(self._usage),
                "model_calls": self._model_calls,
                "usage_reported_calls": self._usage_reported_calls,
                "complete": self._model_calls == self._usage_reported_calls,
            }

    def _start(self, kind: str, serialized: dict[str, Any] | None, **kwargs: Any) -> None:
        """记录组件开始运行的起点时间戳与启动日志。

        :param kind: 组件类别（如 'chain', 'model', 'tool'）。
        :type kind: str
        :param serialized: 组件序列化元数据字典。
        :type serialized: dict[str, typing.Any] | None
        :param kwargs: 包含 run_id 与 parent_run_id 等通用关键字参数。
        :type kwargs: typing.Any
        :return: 无返回值。
        :rtype: None
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：提取 run_id 并在线程锁保护下记录起始高精度时间戳
        run_id = kwargs["run_id"]
        with self._lock:
            self._started[run_id] = perf_counter()
            if kind == "model":
                self._model_calls += 1
        # 第二步：输出结构化启动调试日志
        logger.debug(
            f"{kind}_started",
            extra={
                "event_fields": {
                    "operation_id": str(run_id),
                    "parent_operation_id": str(kwargs["parent_run_id"])
                    if kwargs.get("parent_run_id")
                    else None,
                    "component": kwargs.get("name") or (serialized or {}).get("name"),
                }
            },
        )

    def _finish(
        self,
        kind: str,
        *,
        error: BaseException | None = None,
        failed: bool = False,
        **kwargs: Any,
    ) -> None:
        """记录组件结束运行的耗时、状态分类及审计日志。

        :param kind: 组件类别（如 'chain', 'model', 'tool'）。
        :type kind: str
        :param error: 抛出的底层异常对象。
        :type error: BaseException | None
        :param failed: 是否显式判定为业务逻辑失败。
        :type failed: bool
        :param kwargs: 包含 run_id 与 parent_run_id 等通用关键字参数。
        :type kwargs: typing.Any
        :return: 无返回值。
        :rtype: None
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：线程安全提取并弹出起始时间戳
        run_id = kwargs["run_id"]
        with self._lock:
            started = self._started.pop(run_id, None)
        extra: dict[str, Any] = {
            "operation_id": str(run_id),
            "parent_operation_id": str(kwargs["parent_run_id"])
            if kwargs.get("parent_run_id")
            else None,
        }
        if started is not None:
            extra["duration_ms"] = round((perf_counter() - started) * 1000, 2)

        # 第二步：依据异常类型区分挂起（HITL 中断）、取消、报错与正常完成事件
        if isinstance(error, GraphBubbleUp):
            event, level = f"{kind}_suspended", logging.DEBUG
        elif isinstance(error, (CancelledError, KeyboardInterrupt)):
            event, level = f"{kind}_cancelled", logging.WARNING
        elif error is not None or failed:
            if error is not None:
                extra["error_type"] = type(error).__name__
            event, level = f"{kind}_failed", logging.ERROR
        else:
            event, level = f"{kind}_completed", logging.DEBUG

        # 第三步：记录包含耗时与错误类型的结构化事件日志
        logger.log(level, event, extra={"event_fields": extra})

    def on_chain_start(self, serialized: dict[str, Any] | None, inputs: Any, **kwargs: Any) -> None:
        """执行链或图节点开始运行时的回调入口。

        :param serialized: 链组件序列化信息字典。
        :type serialized: dict[str, typing.Any] | None
        :param inputs: 传入链的输入数据（不读取具体内容）。
        :type inputs: typing.Any
        :param kwargs: 包含 run_id 与 parent_run_id 的关键字参数。
        :type kwargs: typing.Any
        :return: 无返回值。
        :rtype: None
        :author: tongliga
        :date: 2026-10-19
        """
        self._start("chain", serialized, **kwargs)

    def on_chain_end(self, outputs: Any, **kwargs: Any) -> None:
        """执行链或图节点成功运行结束时的回调入口。

        :param outputs: 链执行产出的输出数据。
        :type outputs: typing.Any
        :param kwargs: 包含 run_id 的关键字参数。
        :type kwargs: typing.Any
        :return: 无返回值。
        :rtype: None
        :author: tongliga
        :date: 2026-10-19
        """
        self._finish("chain", **kwargs)

    def on_chain_error(self, error: BaseException, **kwargs: Any) -> None:
        """执行链或图节点发生异常中断时的回调入口。

        :param error: 抛出的底层异常对象。
        :type error: BaseException
        :param kwargs: 包含 run_id 的关键字参数。
        :type kwargs: typing.Any
        :return: 无返回值。
        :rtype: None
        :author: tongliga
        :date: 2026-10-19
        """
        self._finish("chain", error=error, **kwargs)

    def on_chat_model_start(self, serialized: dict[str, Any], messages: Any, **kwargs: Any) -> None:
        """聊天模型（Chat Model）开始发起调用时的回调入口。

        :param serialized: 模型序列化字典。
        :type serialized: dict[str, typing.Any]
        :param messages: 提示词消息列表。
        :type messages: typing.Any
        :param kwargs: 包含 run_id 的关键字参数。
        :type kwargs: typing.Any
        :return: 无返回值。
        :rtype: None
        :author: tongliga
        :date: 2026-10-19
        """
        self._start("model", serialized, **kwargs)

    def on_llm_start(self, serialized: dict[str, Any], prompts: Any, **kwargs: Any) -> None:
        """大语言模型（LLM）开始发起调用时的回调入口。

        :param serialized: 模型序列化字典。
        :type serialized: dict[str, typing.Any]
        :param prompts: 提示词列表。
        :type prompts: typing.Any
        :param kwargs: 包含 run_id 的关键字参数。
        :type kwargs: typing.Any
        :return: 无返回值。
        :rtype: None
        :author: tongliga
        :date: 2026-10-19
        """
        self._start("model", serialized, **kwargs)

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        """大语言模型调用成功完成并返回时的回调入口，累计 Token 消耗。

        :param response: 包含 generations 与 usage_metadata 的模型返回对象。
        :type response: typing.Any
        :param kwargs: 包含 run_id 的关键字参数。
        :type kwargs: typing.Any
        :return: 无返回值。
        :rtype: None
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：安全解析 response 中的 Token 用量元数据
        generations = getattr(response, "generations", [])
        message = getattr(generations[0][0], "message", None) if generations and generations[0] else None
        usage = getattr(message, "usage_metadata", None)
        # 第二步：累计 Token 统计量并记录上报计数
        if usage is not None:
            with self._lock:
                self._usage = add_usage(self._usage, usage)
                self._usage_reported_calls += 1
        # 第三步：记录模型调用完成
        self._finish("model", **kwargs)

    def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        """大语言模型调用发生异常或网络超时中断时的回调入口。

        :param error: 抛出的模型调用异常对象。
        :type error: BaseException
        :param kwargs: 包含 run_id 的关键字参数。
        :type kwargs: typing.Any
        :return: 无返回值。
        :rtype: None
        :author: tongliga
        :date: 2026-10-19
        """
        self._finish("model", error=error, **kwargs)

    def on_tool_start(self, serialized: dict[str, Any], input_str: str, **kwargs: Any) -> None:
        """工具开始执行时的回调入口。

        :param serialized: 工具元数据字典。
        :type serialized: dict[str, typing.Any]
        :param input_str: 工具输入参数字符串。
        :type input_str: str
        :param kwargs: 包含 run_id 的关键字参数。
        :type kwargs: typing.Any
        :return: 无返回值。
        :rtype: None
        :author: tongliga
        :date: 2026-10-19
        """
        self._start("tool", serialized, **kwargs)

    def on_tool_end(self, output: Any, **kwargs: Any) -> None:
        """工具执行结束时的回调入口，识别业务错误状态。

        :param output: 工具执行输出对象。
        :type output: typing.Any
        :param kwargs: 包含 run_id 的关键字参数。
        :type kwargs: typing.Any
        :return: 无返回值。
        :rtype: None
        :author: tongliga
        :date: 2026-10-19
        """
        self._finish("tool", failed=getattr(output, "status", None) == "error", **kwargs)

    def on_tool_error(self, error: BaseException, **kwargs: Any) -> None:
        """工具执行抛出未捕获异常时的回调入口。

        :param error: 抛出的底层异常对象。
        :type error: BaseException
        :param kwargs: 包含 run_id 的关键字参数。
        :type kwargs: typing.Any
        :return: 无返回值。
        :rtype: None
        :author: tongliga
        :date: 2026-10-19
        """
        self._finish("tool", error=error, **kwargs)

    def on_retry(self, retry_state: Any, **kwargs: Any) -> None:
        """操作触发重试机制时的回调入口。

        :param retry_state: 重试状态对象，包含 attempt_number 字段。
        :type retry_state: typing.Any
        :param kwargs: 包含 run_id 的关键字参数。
        :type kwargs: typing.Any
        :return: 无返回值。
        :rtype: None
        :author: tongliga
        :date: 2026-10-19
        """
        logger.debug(
            "operation_retrying",
            extra={
                "event_fields": {
                    "operation_id": str(kwargs["run_id"]),
                    "attempt": retry_state.attempt_number,
                }
            },
        )


__all__ = ["WorkflowLoggingHandler"]
