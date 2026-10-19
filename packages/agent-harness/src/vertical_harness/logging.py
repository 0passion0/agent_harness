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
        """初始化日志格式化器。

        :param json_output: 是否启用标准 JSON 序列化输出。
        :type json_output: bool
        :return: 无返回值。
        :rtype: None
        :author: tongliga
        :date: 2026-10-19
        """
        super().__init__()
        self.json_output = json_output

    def format(self, record: logging.LogRecord) -> str:
        """格式化单条日志记录，脱敏异常细节并注入关联 run_id。

        :param record: 待格式化的标准日志记录对象。
        :type record: logging.LogRecord
        :return: 格式化后的单行日志文本或 JSON 序列化字符串。
        :rtype: str
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：构建基础受控载荷字典
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
            "run_id": _RUN_ID.get(),
        }
        # 第二步：合并调用方显式提交的可公开事件字段
        fields = getattr(record, "event_fields", {})
        payload.update({name: value for name, value in fields.items() if name not in payload})

        # 第三步：脱敏提取异常类型与调用栈位置，忽略具体报错文本
        if record.exc_info and record.exc_info[0] is not None:
            payload["error_type"] = record.exc_info[0].__name__
            frames = []
            traceback = record.exc_info[2]
            while traceback is not None:
                code = traceback.tb_frame.f_code
                frames.append(f"{code.co_filename}:{traceback.tb_lineno}:{code.co_name}")
                traceback = traceback.tb_next
            payload["traceback"] = frames

        # 第四步：按模式序列化为 JSON 或安全转义的键值对单行文本
        if self.json_output:
            return json.dumps(payload, ensure_ascii=False)
        return " ".join(
            f"{key}={json.dumps(value, ensure_ascii=False)}"
            for key, value in payload.items()
            if value is not None
        )


@contextmanager
def log_context(run_id: str) -> Iterator[None]:
    """绑定当前调用的关联 ID，支持协程隔离和嵌套恢复。

    :param run_id: 当前调用的全局关联标识符。
    :type run_id: str
    :return: 协程与线程隔离的上下文管理器迭代器。
    :rtype: collections.abc.Iterator[None]
    :author: tongliga
    :date: 2026-10-19
    """
    # 第一步：在上下文变量中设置当前 run_id 并保存恢复凭证
    token = _RUN_ID.set(run_id)
    try:
        # 第二步：将控制权交还调用方代码块
        yield
    finally:
        # 第三步：退出上下文时安全复位状态
        _RUN_ID.reset(token)


def configure_logging(settings: HarnessSettings) -> None:
    """显式配置 root 日志；重复调用替换自有 handler，保留宿主已有 handler。

    默认仅输出 stderr。输出格式、文件及轮转策略均来自宿主配置。
    应在应用启动时调用，不能在每次请求中重新配置。

    :param settings: 应用全局配置参数实例。
    :type settings: vertical_harness.config.HarnessSettings
    :return: 无返回值。
    :rtype: None
    :author: tongliga
    :date: 2026-10-19
    """
    # 第一步：初始化输出格式化器与目标 handler 收集列表
    formatter = _Formatter(json_output=settings.log_format == "json")
    handlers: list[logging.Handler] = []

    # 第二步：若指定文件路径，创建并配置滚动文件处理器
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

    # 第三步：创建控制台标准错误流处理器并统一绑定格式化器
    console = logging.StreamHandler()
    console.name = f"{_HANDLER_PREFIX}stderr"
    handlers.append(console)
    for handler in handlers:
        handler.setFormatter(formatter)

    # 第四步：安全替换 root logger 中所有属于本框架命名前缀的旧 handler
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
