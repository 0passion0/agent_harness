"""搜索、工作区文件操作与受控 Python 脚本执行智能体微框架核心模块。"""

from vertical_harness.config import HarnessSettings, StoreBackendType, TaskTier
from vertical_harness.context import RunContext
from vertical_harness.engine.callbacks import WorkflowLoggingHandler
from vertical_harness.engine.compose import BASE_PROMPT, aopen_agent, build_agent, open_agent
from vertical_harness.engine.routing import init_configured_model, make_router
from vertical_harness.engine.streaming import StreamEvent, ainvoke_agent, astream_agent
from vertical_harness.logging import configure_logging, log_context
from vertical_harness.observability import LangSmithSettings, tracing_session
from vertical_harness.storage import aopen_checkpointer, aopen_store, open_checkpointer, open_store


def make_checkpoint_config(thread_id: str) -> dict[str, dict[str, str]]:
    """创建单会话线程的原生 LangGraph 检查点配置字典。

    :param thread_id: 会话线程的全局唯一标识符，必须为非空字符串。
    :type thread_id: str
    :return: 包含 configurable.thread_id 的原生 LangGraph 运行配置字典。
    :rtype: dict[str, dict[str, str]]
    :raises ValueError: 当 thread_id 不是非空字符串时抛出。
    :author: tongliga
    :date: 2026-10-21
    """
    # 第一步：校验 thread_id 必须为非空有效字符串
    if not isinstance(thread_id, str) or not thread_id.strip():
        raise ValueError("thread_id must be nonempty")
    # 第二步：构造并返回原生 LangGraph 检查点配置字典
    return {"configurable": {"thread_id": thread_id}}


__all__ = [
    "BASE_PROMPT",
    "HarnessSettings",
    "LangSmithSettings",
    "RunContext",
    "StoreBackendType",
    "StreamEvent",
    "TaskTier",
    "WorkflowLoggingHandler",
    "ainvoke_agent",
    "aopen_agent",
    "aopen_checkpointer",
    "aopen_store",
    "astream_agent",
    "build_agent",
    "configure_logging",
    "init_configured_model",
    "log_context",
    "make_checkpoint_config",
    "make_router",
    "open_agent",
    "open_checkpointer",
    "open_store",
    "tracing_session",
]
