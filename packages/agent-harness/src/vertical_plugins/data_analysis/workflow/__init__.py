"""科研数据分析 LangGraph 工作流子包导出。"""

from typing import Any

from vertical_plugins.data_analysis.workflow.pipeline import (
    DATA_ANALYSIS_WORKFLOW_NAME,
    DataAnalysisInputState,
    DataAnalysisInternalState,
    DataAnalysisOutputState,
    DataAnalysisWorkflow,
    InternalState,
    get_data_analysis_graph,
    input_schema,
    output_schema,
)


def __getattr__(name: str) -> Any:
    """支持按需延迟获取 `graph`。

    :param name: 属性名称。
    :type name: str
    :return: 属性值。
    :rtype: typing.Any
    :raises AttributeError: 访问未知属性时抛出。
    :author: tongliga
    :date: 2026-10-19
    """
    # 第一步：延迟构建默认数据分析状态图
    if name == "graph":
        return get_data_analysis_graph()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "DATA_ANALYSIS_WORKFLOW_NAME",
    "DataAnalysisInputState",
    "DataAnalysisInternalState",
    "DataAnalysisOutputState",
    "DataAnalysisWorkflow",
    "InternalState",
    "get_data_analysis_graph",
    "input_schema",
    "output_schema",
]
