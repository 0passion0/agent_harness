"""Application logging configuration and call-level context."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
from typing import TYPE_CHECKING

from vertical_harness.structured_formatter import _RUN_ID, StructuredFormatter

if TYPE_CHECKING:
    from vertical_harness.config import HarnessSettings

_HANDLER_PREFIX = "vertical_harness."
_Formatter = StructuredFormatter


class HarnessLogging:
    @staticmethod
    @contextmanager
    def log_context(run_id: str) -> Iterator[None]:
        token = _RUN_ID.set(run_id)
        try:
            yield
        finally:
            _RUN_ID.reset(token)

    @staticmethod
    def configure_logging(settings: HarnessSettings) -> None:
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

__all__ = ["configure_logging", "log_context"]
