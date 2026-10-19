"""通过包入口点（entry points）加载的科研数据分析领域插件实现模块。"""

from __future__ import annotations

import logging
from functools import lru_cache
from importlib.resources import files

from data_analysis.evidence import AnalysisEvidence
from data_analysis.tools import DataAnalysisToolkit
from data_analysis.workflow import (
    DATA_ANALYSIS_WORKFLOW_NAME,
    DataAnalysisInputState,
    DataAnalysisInternalState,
    DataAnalysisOutputState,
    InternalState,
    build_data_analysis_workflow,
    build_data_analysis_workflow_contribution,
    graph,
    input_schema,
    output_schema,
)
from vertical_harness.plugins.contracts import (
    BuildServices,
    Exposure,
    PluginContribution,
    PluginManifest,
    SkillAsset,
    ToolContribution,
    WorkflowMode,
)

logger = logging.getLogger(__name__)

# 技能文档在插件包内部的相对定位路径
SKILL_RELATIVE_PATH = "scientific-data-analysis/SKILL.md"


@lru_cache(maxsize=1)
def _load_skill_asset() -> SkillAsset:
    """从包内静态资源读取并缓存 scientific-data-analysis 技能的 SKILL.md 资产。

    :return: 经路径校验的 SkillAsset 实例。
    :rtype: vertical_harness.plugins.contracts.SkillAsset
    :author: tongliga
    :date: 2026-10-19
    """
    # 第一步：记录静态资源读取调试日志
    logger.debug("data_analysis_skill_resource_loading")
    # 第二步：利用 importlib.resources 安全读取包内技能文档
    skill_text = (
        files("data_analysis")
        .joinpath("skills", "scientific-data-analysis", "SKILL.md")
        .read_text(encoding="utf-8")
    )
    # 第三步：组装标准 SkillAsset 资产结构并返回
    asset = SkillAsset(relative_path=SKILL_RELATIVE_PATH, content=skill_text)
    logger.debug(
        "data_analysis_skill_resource_loaded",
        extra={"event_fields": {"skill_path": SKILL_RELATIVE_PATH}},
    )
    return asset


class DataAnalysisPlugin:
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
        :type workflow_mode: vertical_harness.plugins.contracts.WorkflowMode | None
        :param include_tools: 是否同时导出原生分析工具（纯 Supervisor 模式下可设为 False）。
        :type include_tools: bool
        :return: 无返回值。
        :rtype: None
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：记录插件初始化参数调试日志
        logger.debug(
            "data_analysis_plugin_initialized",
            extra={"event_fields": {"include_tools": include_tools}},
        )
        # 第二步：记录工作流模式与工具导出标志状态
        self._workflow_mode = workflow_mode
        self._include_tools = include_tools

    def build(self, services: BuildServices) -> PluginContribution:
        """返回当前插件的 LangGraph 工作流、原生工具、技能与凭证中间件贡献声明。

        :param services: 宿主在构建期注入的服务实例（工作区物理根目录与持久化存储对象）。
        :type services: vertical_harness.plugins.contracts.BuildServices
        :return: 包含工作流、工具、技能资产与系统提示的插件贡献对象。
        :rtype: vertical_harness.plugins.contracts.PluginContribution
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
            instructions=(
                "Built-in scientific data analysis and reporting tools are available. "
                "When profiling tabular data or validating reports, use inspect_table, "
                "read_analysis_guide, and finalize_report, or delegate to 'data_analysis_workflow'. "
                "Do not fabricate results."
            ),
        )


__all__ = [
    "DATA_ANALYSIS_WORKFLOW_NAME",
    "SKILL_RELATIVE_PATH",
    "DataAnalysisInputState",
    "DataAnalysisInternalState",
    "DataAnalysisOutputState",
    "DataAnalysisPlugin",
    "InternalState",
    "build_data_analysis_workflow",
    "build_data_analysis_workflow_contribution",
    "graph",
    "input_schema",
    "output_schema",
]
