"""LangSmith 分布式链路追踪会话生命周期管理模块。

本模块提供 `TracingSession` 上下文管理器：
1. 嵌套上下文复用：检测外层是否已激活追踪，避免重复初始化追踪会话；
2. 优雅停机刷盘：在会话退出时显式调用 `client.flush()`，确保所有在途追踪 Run 数据已完成向 LangSmith 云端或本地代理投递；
3. 开关感知：当追踪未启用时显式注入 `enabled=False` 禁用追踪探针，消除多余的网络开销。
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import closing, contextmanager

from langsmith import tracing_context
from langsmith.run_helpers import get_tracing_context

from vertical_harness.models import LangSmithSettings

# 模块级日志记录器，记录链路追踪的启动、活跃与刷盘事件
logger = logging.getLogger(__name__)


class TracingSession:
    """LangSmith 追踪会话管理器，提供受控环境上下文生命周期支持。

    纯静态工具类，用于包裹智能体图构建与执行的整个执行周期。
    """

    @staticmethod
    @contextmanager
    def tracing_session(settings: LangSmithSettings | None = None) -> Iterator[None]:
        """开启或复用当前进程中的 LangSmith 链路追踪会话上下文。

        若外层上下文已启用了链路追踪，则直接继承外层上下文；
        若配置中禁用了追踪，则激活 `enabled=False` 上下文屏蔽；
        若开启了追踪，则创建专用客户端、绑定目标项目并在退出时刷新网络缓冲区。

        Args:
            settings (LangSmithSettings | None): LangSmith 专用配置实例；若为 None 则自动从环境变量中解析。

        Yields:
            None: 激活链路追踪保护的代码块。
        """
        # 检测外层是否已存在激活的链路追踪上下文，若有则直接复用
        if get_tracing_context().get("enabled") is not None:
            logger.debug("tracing_session_reusing_outer_context")
            yield
            return

        settings = settings or LangSmithSettings()
        # 若未开启追踪，则显式设置禁用上下文并退出
        if not settings.tracing:
            logger.debug("tracing_session_disabled")
            with tracing_context(enabled=False):
                yield
            return

        logger.debug(
            "tracing_session_starting", extra={"event_fields": {"project": settings.project}}
        )
        # 初始化客户端并绑定上下文
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
