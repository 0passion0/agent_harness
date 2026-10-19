"""确定性学术引用核查 LangGraph 工作流子包公共导出。"""

from typing import Any

from vertical_plugins.research_citation.workflow.pipeline import (
    CITATION_WORKFLOW_NAME,
    CitationAuditWorkflow,
    CitationInputState,
    CitationInternalState,
    CitationOutputState,
    InternalState,
    get_citation_graph,
    input_schema,
    output_schema,
)


def __getattr__(name: str) -> Any:
    """按需延迟导出 `graph` 实例。

    :param name: 属性名称。
    :type name: str
    :return: 属性值。
    :rtype: typing.Any
    :raises AttributeError: 访问未知属性时抛出。
    :author: tongliga
    :date: 2026-10-19
    """
    # 第一步：当访问 graph 时延迟调用 get_citation_graph
    if name == "graph":
        return get_citation_graph()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "CITATION_WORKFLOW_NAME",
    "CitationAuditWorkflow",
    "CitationInputState",
    "CitationInternalState",
    "CitationOutputState",
    "InternalState",
    "get_citation_graph",
    "input_schema",
    "output_schema",
]
