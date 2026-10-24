"""系统结构化日志配置与调用级会话上下文管理模块。

本模块提供 `HarnessLogging` 基础设施管理类：
1. 运行会话标识（`run_id`）自动注入：通过 `log_context` 上下文管理器将调用 ID 绑定到当前异步协程或线程的 `ContextVar`；
2. 根日志处理器配置：支持结构化文本（text）或单行 JSON 格式（json），自动配置控制台 stderr 及可选的 `RotatingFileHandler` 磁盘文件滚动；
3. 幂等更新：重复配置时自动安全替换并关闭旧有的专用日志处理器，避免日志重复打印。
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
from typing import TYPE_CHECKING

from vertical_harness.utils.structured_formatter import _RUN_ID, _THREAD_ID, StructuredFormatter

if TYPE_CHECKING:
    from vertical_harness.models import HarnessSettings

# 专属于 vertical_harness 的日志处理器名称前缀，便于识别和清理
_HANDLER_PREFIX: str = "vertical_harness."


class HarnessLogging:
    """系统日志基础设施管理器，负责统一控制台与滚动文件日志格式。

    纯静态工具类，提供会话追踪上下文切换与全局日志树配置。
    """

    @staticmethod
    @contextmanager
    def log_context(run_id: str, *, thread_id: str | None = None) -> Iterator[None]:
        """设置当前协程/线程上下文中的 run_id 标识符，并在退出时重置。

        Args:
            run_id (str): 本次运行调用的唯一追踪 ID 字符串。

        Yields:
            None: 激活该运行标识绑定的代码上下文。
        """
        token = _RUN_ID.set(run_id)
        thread_token = _THREAD_ID.set(thread_id if thread_id is not None else _THREAD_ID.get())
        try:
            yield
        finally:
            _RUN_ID.reset(token)
            _THREAD_ID.reset(thread_token)

    @staticmethod
    def configure_logging(settings: HarnessSettings) -> None:
        """根据宿主配置初始化或重置全局根日志处理器与格式化器。

        设置全局日志过滤等级（DEBUG, INFO 等），挂载 stderr 控制台处理器以及可选的
        带自动大小滚动（`RotatingFileHandler`）的文件处理器，旧有的同名前缀处理器将被妥善关闭并移除。

        Args:
            settings (HarnessSettings): 宿主配置实例，读取 `log_level`, `log_format`,
                `log_file`, `log_max_bytes` 及 `log_backup_count`。
        """
        formatter = StructuredFormatter(json_output=settings.log_format == "json")
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
