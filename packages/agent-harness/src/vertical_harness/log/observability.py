"""LangSmith 分布式链路追踪会话生命周期管理模块。"""

import logging
from collections.abc import Iterator
from contextlib import closing, contextmanager

from langsmith import tracing_context
from langsmith.run_helpers import get_tracing_context

from vertical_harness.config.langsmith_settings import LangSmithSettings

logger = logging.getLogger(__name__)


class TracingSession:
    """LangSmith 追踪会话管理器，提供受控环境上下文生命周期支持。

    :author: tongliga
    :date: 2026-10-21
    """

    @staticmethod
    @contextmanager
    def tracing_session(settings: LangSmithSettings | None = None) -> Iterator[None]:
        """开启或复用当前进程中的 LangSmith 链路追踪会话上下文。

        :param settings: 可选的 LangSmith 配置实例，未提供时默认从环境中自动加载。
        :type settings: vertical_harness.config.langsmith_settings.LangSmithSettings | None
        :return: 产生链路追踪上下文的作用域迭代器。
        :rtype: collections.abc.Iterator[None]
        :author: tongliga
        :date: 2026-10-21
        """
        # 第一步：若外层已有活动的链路追踪上下文，则直接复用
        if get_tracing_context().get("enabled") is not None:
            logger.debug("tracing_session_reusing_outer_context")
            yield
            return

        # 第二步：加载配置并检查是否显式开启链路追踪
        settings = settings or LangSmithSettings()
        if not settings.tracing:
            logger.debug("tracing_session_disabled")
            with tracing_context(enabled=False):
                yield
            return

        # 第三步：构建专用客户端，并开启作用域追踪
        logger.debug("tracing_session_starting", extra={"event_fields": {"project": settings.project}})
        with closing(settings.create_client()) as client:
            try:
                with tracing_context(enabled=True, client=client, project_name=settings.project):
                    logger.info(
                        "tracing_session_active", extra={"event_fields": {"project": settings.project}}
                    )
                    yield
            finally:
                # 第四步：在会话退出时刷新未发送追踪缓冲数据
                logger.debug("tracing_session_flushing")
                client.flush()
                logger.debug("tracing_session_closed")
