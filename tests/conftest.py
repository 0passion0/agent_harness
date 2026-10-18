"""隔离显式日志配置，避免 CLI 测试把 handler 留在已关闭的捕获流上。"""

import logging

import pytest


@pytest.fixture(autouse=True)
def restore_project_logging():
    loggers = [logging.getLogger()]
    snapshots = [(logger, logger.handlers[:], logger.level, logger.propagate) for logger in loggers]
    yield
    added = set()
    for logger, handlers, level, propagate in snapshots:
        added.update(set(logger.handlers) - set(handlers))
        logger.handlers[:] = handlers
        logger.setLevel(level)
        logger.propagate = propagate
    for handler in added:
        handler.close()
