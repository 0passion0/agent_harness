"""应用日志配置、结构化输出与调用级上下文；导入时不配置全局 logging。"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from vertical_harness.config import HarnessSettings

_HANDLER_PREFIX = "vertical_harness."
_RUN_ID: ContextVar[str | None] = ContextVar("harness_log_run_id", default=None)


class _Formatter(logging.Formatter):
    """仅输出受控字段；异常只保留类型与调用位置，不输出可能含敏感输入的消息。"""

    def __init__(self, *, json_output: bool) -> None:
        super().__init__()
        self.json_output = json_output

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
            "run_id": _RUN_ID.get(),
        }
        # 调用方显式提交可公开的事件字段，日志基础设施不认识领域对象。
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
        # JSON 转义同时防止换行和终端控制字符伪造日志行。
        return " ".join(
            f"{key}={json.dumps(value, ensure_ascii=False)}"
            for key, value in payload.items()
            if value is not None
        )


@contextmanager
def log_context(run_id: str) -> Iterator[None]:
    """绑定当前调用的关联 ID，支持协程隔离和嵌套恢复。"""
    token = _RUN_ID.set(run_id)
    try:
        yield
    finally:
        _RUN_ID.reset(token)


def configure_logging(settings: HarnessSettings) -> None:
    """显式配置 root 日志；重复调用替换自有 handler，保留宿主已有 handler。

    默认仅输出 stderr。输出格式、文件及轮转策略均来自宿主配置。
    应在应用启动时调用，不能在每次请求中重新配置。
    """
    formatter = _Formatter(json_output=settings.log_format == "json")
    handlers: list[logging.Handler] = []
    if settings.log_file is not None:
        path = settings.log_file.expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            path,
            maxBytes=settings.log_max_bytes,
            backupCount=settings.log_backup_count,
            encoding="utf-8",
        )
        file_handler.name = f"{_HANDLER_PREFIX}file"
        handlers.append(file_handler)
    console = logging.StreamHandler()
    console.name = f"{_HANDLER_PREFIX}stderr"
    handlers.append(console)
    for handler in handlers:
        handler.setFormatter(formatter)

    # 仅由应用入口显式配置 root，任意插件的标准 logging 均可自动接入。
    logger = logging.getLogger()
    previous = []
    for handler in logger.handlers[:]:
        if handler.name and handler.name.startswith(_HANDLER_PREFIX):
            logger.removeHandler(handler)
            previous.append(handler)
    logger.setLevel(settings.log_level)
    for handler in handlers:
        handler.setLevel(settings.log_level)
        logger.addHandler(handler)
    for handler in previous:
        handler.close()


__all__ = ["configure_logging", "log_context"]
