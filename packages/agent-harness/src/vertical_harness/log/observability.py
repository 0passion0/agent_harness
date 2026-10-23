"""LangSmith 分布式链路追踪会话生命周期管理模块。"""

import logging
from collections.abc import Iterator
from contextlib import closing, contextmanager

from langsmith import tracing_context
from langsmith.run_helpers import get_tracing_context

from vertical_harness.config.langsmith_settings import LangSmithSettings

logger = logging.getLogger(__name__)


class TracingSession:
    """LangSmith 追踪会话管理器，提供受控环境上下文生命周期支持。"""

    @staticmethod
    @contextmanager
    def tracing_session(settings: LangSmithSettings | None = None) -> Iterator[None]:
        """开启或复用当前进程中的 LangSmith 链路追踪会话上下文。"""
        if get_tracing_context().get("enabled") is not None:
            logger.debug("tracing_session_reusing_outer_context")
            yield
            return
        settings = settings or LangSmithSettings()
        if not settings.tracing:
            logger.debug("tracing_session_disabled")
            with tracing_context(enabled=False):
                yield
            return
        logger.debug(
            "tracing_session_starting", extra={"event_fields": {"project": settings.project}}
        )
        with closing(settings.create_client()) as client:
            try:
                with tracing_context(enabled=True, client=client, project_name=settings.project):
                    logger.info(
                        "tracing_session_active",
                        extra={"event_fields": {"project": settings.project}},
                    )
                    yield
            finally:
                logger.debug("tracing_session_flushing")
                client.flush()
                logger.debug("tracing_session_closed")
