"""仅通过包入口点（entry point）加载的独立领域插件实现模块。"""

from __future__ import annotations

import logging
from functools import lru_cache
from importlib.resources import files

from research_citation.checker.tools import ResearchCitationToolkit
from research_citation.workflow import (
    CITATION_WORKFLOW_NAME,
    CitationInputState,
    CitationInternalState,
    CitationOutputState,
    InternalState,
    build_citation_workflow,
    build_citation_workflow_contribution,
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

SKILL_RELATIVE_PATH = "numeric-citation-audit/SKILL.md"
logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _load_skill_asset() -> SkillAsset:
    """从包内静态资源读取并缓存 numeric-citation-audit 技能的 SKILL.md 资产。

    :return: 经路径校验的 SkillAsset 实例。
    """
    logger.debug("skill_resource_loading")
    skill_text = (
        files("research_citation")
        .joinpath("skills", "numeric-citation-audit", "SKILL.md")
        .read_text(encoding="utf-8")
    )
    asset = SkillAsset(relative_path=SKILL_RELATIVE_PATH, content=skill_text)
    logger.debug(
        "skill_resource_loaded", extra={"event_fields": {"skill_path": SKILL_RELATIVE_PATH}}
    )
    return asset


class ResearchCitationPlugin:
    """学术引用审计插件实现类；同时提供 LangGraph 确定性工作流插件与技能工具声明。"""

    # 插件元数据声明清单
    manifest = PluginManifest(
        plugin_id="research.citations",
        api_version=1,
        version="0.1.0",
        description="Deterministic in-text reference checks for research manuscripts",
    )

    def __init__(
        self,
        *,
        workflow_mode: WorkflowMode | None = None,
        include_tools: bool = True,
    ) -> None:
        """初始化引用审计插件。

        :param workflow_mode: 可选的工作流接入模式覆盖（compiled 或 async）。
        :param include_tools: 是否同时导出技能门控工具（纯 Supervisor 模式下可设为 False）。
        """
        logger.debug(
            "citation_plugin_initialized",
            extra={"event_fields": {"include_tools": include_tools}},
        )
        self._workflow_mode = workflow_mode
        self._include_tools = include_tools

    def build(self, services: BuildServices) -> PluginContribution:
        """返回当前插件的 LangGraph 工作流、原生工具与技能文件声明，无需修改宿主核心逻辑。

        :param services: 宿主在构建期注入的服务实例（如工作区路径与存储对象）。
        :return: 包含工作流、工具、技能资产与提示指令的声明式 Deep Agents 插件贡献对象。
        """
        toolkit = ResearchCitationToolkit()
        logger.debug("citation_plugin_build_started")
        return PluginContribution(
            workflows=[build_citation_workflow_contribution(mode=self._workflow_mode)],
            tools=(
                ToolContribution.from_toolkit(toolkit, exposure=Exposure.SKILL)
                if self._include_tools
                else []
            ),
            skills=[_load_skill_asset()],
            instructions=(
                "Delegate numeric citation checks to the 'citation_audit' workflow or "
                "use numeric-citation-audit skill for bracket-numbered references. "
                "Report only verified inconsistencies."
            ),
        )


__all__ = [
    "CITATION_WORKFLOW_NAME",
    "SKILL_RELATIVE_PATH",
    "CitationInputState",
    "CitationInternalState",
    "CitationOutputState",
    "InternalState",
    "ResearchCitationPlugin",
    "build_citation_workflow",
    "build_citation_workflow_contribution",
    "graph",
    "input_schema",
    "output_schema",
]
