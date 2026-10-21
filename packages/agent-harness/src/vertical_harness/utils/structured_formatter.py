"""受控结构化日志格式化器模块，支持纯文本键值对与 JSON 双模输出。"""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

_RUN_ID: ContextVar[str | None] = ContextVar("harness_log_run_id", default=None)


class StructuredFormatter(logging.Formatter):
    """输出受控字段并脱敏/标准化异常堆栈信息的结构化日志格式化器。

    :author: tongliga
    :date: 2026-10-21
    """

    def __init__(self, *, json_output: bool) -> None:
        """初始化结构化日志格式化器。

        :param json_output: 是否以单行 JSON 格式输出日志，False 则输出键值对。
        :type json_output: bool
        :author: tongliga
        :date: 2026-10-21
        """
        super().__init__()
        self.json_output = json_output

    def format(self, record: logging.LogRecord) -> str:
        """格式化单条 LogRecord 日志记录。

        :param record: 标准日志记录实体。
        :type record: logging.LogRecord
        :return: 序列化后的日志字符串。
        :rtype: str
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：提取基础字段与上下文中的 run_id
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

        # 第二步：若存在异常信息，提取受控异常类别与堆栈帧列表
        if record.exc_info and record.exc_info[0] is not None:
            payload["error_type"] = record.exc_info[0].__name__
            frames = []
            traceback = record.exc_info[2]
            while traceback is not None:
                code = traceback.tb_frame.f_code
                frames.append(f"{code.co_filename}:{traceback.tb_lineno}:{code.co_name}")
                traceback = traceback.tb_next
            payload["traceback"] = frames

        # 第三步：按配置输出 JSON 或紧凑 key=value 键值对字符串
        if self.json_output:
            return json.dumps(payload, ensure_ascii=False)
        return " ".join(
            f"{key}={json.dumps(value, ensure_ascii=False)}"
            for key, value in payload.items()
            if value is not None
        )


__all__ = ["_RUN_ID", "StructuredFormatter"]
