"""系统结构化日志配置与调用级会话上下文管理模块。"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
from typing import TYPE_CHECKING

from vertical_harness.utils.structured_formatter import _RUN_ID, StructuredFormatter

if TYPE_CHECKING:
    from vertical_harness.config.settings import HarnessSettings

_HANDLER_PREFIX = "vertical_harness."
_Formatter = StructuredFormatter


class HarnessLogging:
    """系统日志基础设施管理器，负责统一控制台与滚动文件日志格式。

    :author: tongliga
    :date: 2026-10-21
    """

    @staticmethod
    @contextmanager
    def log_context(run_id: str) -> Iterator[None]:
        """设置当前协程/线程上下文中的 run_id 标识符，并在退出时重置。

        :param run_id: 每次执行运行的唯一标识符字符串。
        :type run_id: str
        :return: 上下文管理器生成器，无产出值。
        :rtype: collections.abc.Iterator[None]
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：在上下文变量中写入当前 run_id 并保留 token
        token = _RUN_ID.set(run_id)
        try:
            yield
        finally:
            # 第二步：在离开上下文作用域时安全还原 token
            _RUN_ID.reset(token)

    @staticmethod
    def configure_logging(settings: HarnessSettings) -> None:
        """根据宿主配置初始化或重置全局根日志处理器与格式化器。

        :param settings: 包含日志文件路径、级别与格式配置的宿主设置对象。
        :type settings: vertical_harness.config.settings.HarnessSettings
        :return: 无返回值。
        :rtype: None
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：根据配置选用 JSON 或纯文本结构化格式化器
        formatter = _Formatter(json_output=settings.log_format == "json")
        handlers: list[logging.Handler] = []

        # 第二步：如果配置了日志文件路径，创建并附加滚动文件处理器
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

        # 第三步：创建标准错误输出控制台处理器
        console = logging.StreamHandler()
        console.name = f"{_HANDLER_PREFIX}stderr"
        handlers.append(console)
        for handler in handlers:
            handler.setFormatter(formatter)

        # 第四步：清理已有带专属前缀的旧处理器并应用新处理器集合
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


configure_logging = HarnessLogging.configure_logging
log_context = HarnessLogging.log_context

__all__ = ["HarnessLogging", "configure_logging", "log_context"]
