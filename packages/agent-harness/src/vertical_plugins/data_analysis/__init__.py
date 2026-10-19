"""Data Analysis 科研数据分析内置插件：入口类、清单与装配入口。

本文件是插件根目录唯一的 Python 文件；数据契约、工具、提示词与工作流实现均位于子包：
- `vertical_plugins.data_analysis.models`: 表格画像、分析任务输入、报告校验与执行凭证 Pydantic 强类型模型
- `vertical_plugins.data_analysis.prompts`: 精简的分析计划与中文报告生成提示词
- `vertical_plugins.data_analysis.tools`: 基于 DuckDB 与 OpenPyXL 的表格画像、报告一致性校验发布及内部工具集
- `vertical_plugins.data_analysis.workflow`: 继承 `BaseWorkflow` 的六阶段固定数据分析 LangGraph 工作流
- `vertical_plugins.data_analysis.skills`: 随插件分发的技能资产
"""

from __future__ import annotations

import logging
from typing import Any

from vertical_plugins.common.base import WorkflowPlugin
from vertical_plugins.common.contracts import (
    BuildServices,
    Exposure,
    PluginContribution,
    PluginManifest,
    ToolContribution,
    WorkflowMode,
)
from vertical_plugins.data_analysis.models import (
    AnalysisEvidence,
    AnalysisState,
    AnalysisStateModel,
    AnalysisTaskInput,
    AnalysisTypeLiteral,
    ColumnProfile,
    ColumnValueCount,
    ExecutionReceipt,
    NumericSubset,
    ReportValidationInput,
    ReportValidationResult,
    StrictModel,
    TableProfileInput,
    TableProfileResult,
    artifact_paths,
    merge_executions,
    snapshot,
)
from vertical_plugins.data_analysis.prompts import (
    ANALYSIS_INTERPRETATION_PROMPT,
    ANALYSIS_PLAN_PROMPT,
    CORE_REPORT_SECTIONS,
    DATA_ANALYSIS_INSTRUCTIONS,
    REPORT_GENERATION_PROMPT,
    build_analysis_plan_prompt,
    build_report_prompt,
)
from vertical_plugins.data_analysis.tools import (
    PROFILE_CONTEXT_LIMIT,
    PROFILE_PAGE_SIZE,
    REPORT_SECTIONS,
    DataAnalysisToolkit,
    profile_table,
    save_report,
    validate_report,
)
from vertical_plugins.data_analysis.workflow import (
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

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

# 技能文档在插件包内部的相对定位路径
SKILL_RELATIVE_PATH = "scientific-data-analysis/SKILL.md"


class DataAnalysisPlugin(WorkflowPlugin):
    """科研数据分析工作流插件；继承 `WorkflowPlugin` 统一管理技能发现、上下文注入与工作流编译。

    初始化阶段默认仅向主 Agent 暴露技能资产与受控工作流声明，不直接暴露内部 `DataAnalysisToolkit` 全量工具，
    也不向主 Agent 全局注入 `AnalysisEvidence` 中间件。
    """

    manifest = PluginManifest(
        plugin_id="data_analysis",
        api_version=1,
        version="0.1.0",
        description="科研数据表格分析，包含数据画像、执行凭证与报告生成",
    )
    workflow_cls = DataAnalysisWorkflow
    instructions_template = DATA_ANALYSIS_INSTRUCTIONS
    expose_tools_by_default = False

    def __init__(
        self,
        *,
        workflow_mode: WorkflowMode | None = None,
        include_tools: bool = False,
    ) -> None:
        """初始化科研数据分析插件。

        :param workflow_mode: 可选的工作流接入模式覆盖（compiled 或 async）。
        :type workflow_mode: vertical_plugins.common.contracts.WorkflowMode | None
        :param include_tools: 是否显式导出底层工具集，默认 False（遵循 P5 最小暴露原则）。
        :type include_tools: bool
        :return: 无返回值。
        :rtype: None
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：保存工作流运行模式与工具暴露开关
        self._workflow_mode = workflow_mode
        self._include_tools = include_tools

    def build_tools(self, services: BuildServices) -> tuple[ToolContribution, ...]:
        """按需构造数据分析内部工具包贡献。

        :param services: 宿主构建服务。
        :type services: vertical_plugins.common.contracts.BuildServices
        :return: 工具贡献元组。
        :rtype: tuple[vertical_plugins.common.contracts.ToolContribution, ...]
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：实例化绑定工作区路径的 DataAnalysisToolkit 并封装为 SKILL 级工具
        toolkit = DataAnalysisToolkit(services.workspace_root)
        return tuple(ToolContribution.from_toolkit(toolkit, exposure=Exposure.SKILL))

    def build(self, services: BuildServices) -> PluginContribution:
        """统一组装数据分析插件的工作流、技能与系统指令贡献（无全局中间件污染）。

        :param services: 宿主在构建期注入的服务实例。
        :type services: vertical_plugins.common.contracts.BuildServices
        :return: 插件贡献对象。
        :rtype: vertical_plugins.common.contracts.PluginContribution
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：创建注入了宿主工作区与配置上下文的 DataAnalysisWorkflow
        workflow = self.create_workflow(services)
        skills = self.discover_skills()
        tools = (
            self.build_tools(services)
            if (self._include_tools or self.expose_tools_by_default)
            else ()
        )
        instructions = self.build_instructions(services)

        # 第二步：组装并返回纯净的 PluginContribution
        return PluginContribution(
            workflows=[workflow.to_contribution(mode=self._workflow_mode)],
            tools=list(tools),
            skills=list(skills),
            instructions=instructions,
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
    "ANALYSIS_INTERPRETATION_PROMPT",
    "ANALYSIS_PLAN_PROMPT",
    "CORE_REPORT_SECTIONS",
    "DATA_ANALYSIS_INSTRUCTIONS",
    "DATA_ANALYSIS_WORKFLOW_NAME",
    "PROFILE_CONTEXT_LIMIT",
    "PROFILE_PAGE_SIZE",
    "REPORT_GENERATION_PROMPT",
    "REPORT_SECTIONS",
    "SKILL_RELATIVE_PATH",
    "AnalysisEvidence",
    "AnalysisState",
    "AnalysisStateModel",
    "AnalysisTaskInput",
    "AnalysisTypeLiteral",
    "ColumnProfile",
    "ColumnValueCount",
    "DataAnalysisInputState",
    "DataAnalysisInternalState",
    "DataAnalysisOutputState",
    "DataAnalysisPlugin",
    "DataAnalysisToolkit",
    "DataAnalysisWorkflow",
    "ExecutionReceipt",
    "InternalState",
    "NumericSubset",
    "ReportValidationInput",
    "ReportValidationResult",
    "StrictModel",
    "TableProfileInput",
    "TableProfileResult",
    "artifact_paths",
    "build_analysis_plan_prompt",
    "build_report_prompt",
    "get_data_analysis_graph",
    "input_schema",
    "merge_executions",
    "output_schema",
    "profile_table",
    "save_report",
    "snapshot",
    "validate_report",
]
