"""受控结构化日志格式化器模块，支持纯文本键值对与 JSON 双模输出。

本模块提供 `StructuredFormatter` 日志格式化器：
1. 上下文追踪集成：自动从 `_RUN_ID` ContextVar 提取调用运行 ID 注入日志载荷；
2. 异常堆栈结构化提取：自动解析 `exc_info` 提取异常类名与堆栈调用帧列表 `traceback`；
3. 双模支持：支持控制台友好的 `key="value"` 键值行输出，或文件归档专用的单行紧凑 JSON 格式输出。
"""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

# 记录当前执行协程或线程所属 run_id 的上下文变量，用于日志链路关联
_RUN_ID: ContextVar[str | None] = ContextVar("harness_log_run_id", default=None)


class StructuredFormatter(logging.Formatter):
    """输出受控字段并脱敏/标准化异常堆栈信息的结构化日志格式化器。

    Attributes:
        json_output (bool): 是否启用结构化 JSON 格式输出；若为 False 则格式化为可读的 key=value 纯文本行。
    """

    def __init__(self, *, json_output: bool) -> None:
        """初始化结构化日志格式化器。

        Args:
            json_output (bool): 若为 True 则输出 JSON 单行字符串，否则输出键值对文本。
        """
        super().__init__()
        self.json_output = json_output

    def format(self, record: logging.LogRecord) -> str:
        """格式化单条 LogRecord 日志记录。

        提取 UTC 时间戳（ISO-8601 毫秒级）、日志等级、记录器名称、消息正文、当前关联的 run_id，
        以及通过 `extra={"event_fields": ...}` 传入的业务字段。

        Args:
            record (logging.LogRecord): 标准日志记录对象。

        Returns:
            str: 格式化后的单行文本或单行 JSON 字符串。
        """
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
            "run_id": _RUN_ID.get(),
        }
        fields = getattr(record, "event_fields", {})
        payload.update({name: value for name, value in fields.items() if name not in payload})
        if record.exc_info and record.exc_info[0] is not None:
            payload["error_type"] = record.exc_info[0].__name__
            frames = []
            traceback = record.exc_info[2]
            while traceback is not None:
                code = traceback.tb_frame.f_code
                frames.append(f"{code.co_filename}:{traceback.tb_lineno}:{code.co_name}")
                traceback = traceback.tb_next
            payload["traceback"] = frames
        if self.json_output:
            return json.dumps(payload, ensure_ascii=False)
        return " ".join(
            f"{key}={json.dumps(value, ensure_ascii=False)}"
            for key, value in payload.items()
            if value is not None
        )
