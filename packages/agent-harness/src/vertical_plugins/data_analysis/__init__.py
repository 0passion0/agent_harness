"""Data Analysis 科研数据分析内置插件：入口类、清单与装配入口。

本文件是插件根目录唯一的 Python 文件；算法、工具、提示词与工作流实现均位于子包：
- `vertical_plugins.data_analysis.models`: 表格画像、报告校验与执行凭证 Pydantic 强类型模型
- `vertical_plugins.data_analysis.tools`: LangChain 原生工具集、基于 DuckDB 与 OpenPyXL 的表格画像算法与中文科研报告校验发布
- `vertical_plugins.data_analysis.workflow`: 8 阶段确定性全流程 LangGraph 管道与 `DataAnalysisWorkflow` 类封装
- `vertical_plugins.data_analysis.prompts`: 插件系统指令、工作流固定节点提示词与科研规范指南读取
- `vertical_plugins.data_analysis.runtime`: 执行凭证中间件、节点重试/分叉/检查点 Hook 与桌面端状态投影
- `vertical_plugins.data_analysis.guides` / `skills`: 随插件分发的只读指南与技能资产
"""

from __future__ import annotations

import logging
from functools import lru_cache
from importlib.resources import files

from vertical_plugins.common.base import DomainPlugin
from vertical_plugins.common.contracts import (
    BuildServices,
    Exposure,
    PluginContribution,
    PluginManifest,
    SkillAsset,
    ToolContribution,
    WorkflowMode,
)
from vertical_plugins.data_analysis.models import (
    AnalysisStateModel,
    ColumnProfile,
    ColumnValueCount,
    ExecutionReceipt,
    NumericSubset,
    ReportValidationInput,
    ReportValidationResult,
    StrictModel,
    TableProfileInput,
    TableProfileResult,
)
from vertical_plugins.data_analysis.prompts import (
    AUDIT_EVIDENCE_NODE_PROMPT,
    CLARIFY_RESEARCH_NODE_PROMPT,
    DATA_ANALYSIS_INSTRUCTIONS,
    DRAFT_REPORT_NODE_PROMPT,
    EXECUTE_ANALYSIS_NODE_PROMPT,
    FINALIZE_OUTPUT_NODE_PROMPT,
    GUIDE_DESCRIPTIONS,
    INPUT_VALIDATION_NODE_PROMPT,
    NODE_AUDIT_EVIDENCE,
    NODE_CLARIFY_RESEARCH,
    NODE_DRAFT_REPORT,
    NODE_EXECUTE_ANALYSIS,
    NODE_FINALIZE_OUTPUT,
    NODE_INPUT_VALIDATION,
    NODE_PROFILE_TABLE,
    NODE_PROMPTS_MAP,
    NODE_VALIDATE_REPORT,
    PROFILE_NODE_PROMPT,
    VALIDATE_REPORT_NODE_PROMPT,
    GuideName,
    get_node_prompt,
    read_analysis_guide,
)
from vertical_plugins.data_analysis.runtime import (
    DESCRIPTOR,
    AnalysisEvidence,
    AnalysisState,
    artifact_paths,
    fork_node_branch,
    inspect_node_checkpoints,
    merge_executions,
    modify_node_state,
    retry_node,
    snapshot,
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
    build_data_analysis_workflow,
    build_data_analysis_workflow_contribution,
    graph,
    input_schema,
    output_schema,
)

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

# 技能文档在插件包内部的相对定位路径
SKILL_RELATIVE_PATH = "scientific-data-analysis/SKILL.md"


@lru_cache(maxsize=1)
def _load_skill_asset() -> SkillAsset:
    """从包内静态资源读取并缓存 scientific-data-analysis 技能的 SKILL.md 资产。

    :return: 经路径校验的 SkillAsset 实例。
    :rtype: vertical_plugins.common.contracts.SkillAsset
    :author: tongliga
    :date: 2026-10-19
    """
    logger.debug("data_analysis_skill_resource_loading")
    # ponytail: 使用标准库 importlib.resources 安全读取技能文档
    skill_text = (
        files("vertical_plugins.data_analysis")
        .joinpath("skills", "scientific-data-analysis", "SKILL.md")
        .read_text(encoding="utf-8")
    )
    asset = SkillAsset(relative_path=SKILL_RELATIVE_PATH, content=skill_text)
    logger.debug(
        "data_analysis_skill_resource_loaded",
        extra={"event_fields": {"skill_path": SKILL_RELATIVE_PATH}},
    )
    return asset


class DataAnalysisPlugin(DomainPlugin):
    """科研数据分析插件实现类；提供 LangGraph 确定性工作流、分析工具集、执行凭证中间件与技能文档。"""

    # 插件元数据声明清单
    manifest = PluginManifest(
        plugin_id="data_analysis",
        api_version=1,
        version="0.1.0",
        description="Scientific tabular data analysis, table profiling, execution evidence, and reporting",
    )

    def __init__(
        self,
        *,
        workflow_mode: WorkflowMode | None = None,
        include_tools: bool = True,
    ) -> None:
        """初始化科研数据分析插件。

        :param workflow_mode: 可选的工作流接入模式覆盖（compiled 或 async）。
        :type workflow_mode: vertical_plugins.common.contracts.WorkflowMode | None
        :param include_tools: 是否同时导出原生分析工具（纯 Supervisor 模式下可设为 False）。
        :type include_tools: bool
        :return: 无返回值。
        :rtype: None
        :author: tongliga
        :date: 2026-10-19
        """
        logger.debug(
            "data_analysis_plugin_initialized",
            extra={"event_fields": {"include_tools": include_tools}},
        )
        self._workflow_mode = workflow_mode
        self._include_tools = include_tools

    def build(self, services: BuildServices) -> PluginContribution:
        """返回当前插件的 LangGraph 工作流、原生工具、技能与凭证中间件贡献声明。

        :param services: 宿主在构建期注入的服务实例（工作区物理根目录与持久化存储对象）。
        :type services: vertical_plugins.common.contracts.BuildServices
        :return: 包含工作流、工具、技能资产与系统提示的插件贡献对象。
        :rtype: vertical_plugins.common.contracts.PluginContribution
        :author: tongliga
        :date: 2026-10-19
        """
        logger.info(
            "data_analysis_plugin_build_started",
            extra={"event_fields": {"workspace_root": str(services.workspace_root)}},
        )
        # 第一步：实例化数据分析工具包与执行凭证采集中间件
        toolkit = DataAnalysisToolkit(services.workspace_root)
        evidence = AnalysisEvidence(services.workspace_root)

        # 第二步：组装标准 PluginContribution 并返回
        return PluginContribution(
            workflows=[
                build_data_analysis_workflow_contribution(
                    mode=self._workflow_mode,
                )
            ],
            tools=(
                ToolContribution.from_toolkit(toolkit, exposure=Exposure.EAGER)
                if self._include_tools
                else []
            ),
            skills=[_load_skill_asset()],
            middleware=[evidence],
            instructions=DATA_ANALYSIS_INSTRUCTIONS,
        )


__all__ = [
    "AUDIT_EVIDENCE_NODE_PROMPT",
    "CLARIFY_RESEARCH_NODE_PROMPT",
    "DATA_ANALYSIS_INSTRUCTIONS",
    "DATA_ANALYSIS_WORKFLOW_NAME",
    "DESCRIPTOR",
    "DRAFT_REPORT_NODE_PROMPT",
    "EXECUTE_ANALYSIS_NODE_PROMPT",
    "FINALIZE_OUTPUT_NODE_PROMPT",
    "GUIDE_DESCRIPTIONS",
    "INPUT_VALIDATION_NODE_PROMPT",
    "NODE_AUDIT_EVIDENCE",
    "NODE_CLARIFY_RESEARCH",
    "NODE_DRAFT_REPORT",
    "NODE_EXECUTE_ANALYSIS",
    "NODE_FINALIZE_OUTPUT",
    "NODE_INPUT_VALIDATION",
    "NODE_PROFILE_TABLE",
    "NODE_PROMPTS_MAP",
    "NODE_VALIDATE_REPORT",
    "PROFILE_CONTEXT_LIMIT",
    "PROFILE_NODE_PROMPT",
    "PROFILE_PAGE_SIZE",
    "REPORT_SECTIONS",
    "SKILL_RELATIVE_PATH",
    "VALIDATE_REPORT_NODE_PROMPT",
    "AnalysisEvidence",
    "AnalysisState",
    "AnalysisStateModel",
    "ColumnProfile",
    "ColumnValueCount",
    "DataAnalysisInputState",
    "DataAnalysisInternalState",
    "DataAnalysisOutputState",
    "DataAnalysisPlugin",
    "DataAnalysisToolkit",
    "DataAnalysisWorkflow",
    "ExecutionReceipt",
    "GuideName",
    "InternalState",
    "NumericSubset",
    "ReportValidationInput",
    "ReportValidationResult",
    "StrictModel",
    "TableProfileInput",
    "TableProfileResult",
    "artifact_paths",
    "build_data_analysis_workflow",
    "build_data_analysis_workflow_contribution",
    "fork_node_branch",
    "get_node_prompt",
    "graph",
    "input_schema",
    "inspect_node_checkpoints",
    "merge_executions",
    "modify_node_state",
    "output_schema",
    "profile_table",
    "read_analysis_guide",
    "retry_node",
    "save_report",
    "snapshot",
    "validate_report",
]
