"""Research Citation 学术引用审计内置插件：入口类、清单与装配入口。

本文件是插件根目录唯一的 Python 文件；算法、工具、提示词与工作流实现均位于子包：
- `vertical_plugins.research_citation.models`: 引用核查输入/输出契约与证据驱动的学术审计报告 Pydantic 模型
- `vertical_plugins.research_citation.checker`: 纯函数式引用解析器、确定性比对评估器与 LangChain `BaseToolkit` 工具集
- `vertical_plugins.research_citation.workflow`: 遵循三层状态契约（`InternalState` / `input_schema` / `output_schema`）的确定性 LangGraph 工作流
- `vertical_plugins.research_citation.prompts`: 插件系统指令
- `vertical_plugins.research_citation.skills`: 随插件分发的技能资产
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
from vertical_plugins.research_citation.checker import (
    MAX_CITATION_SPAN,
    ResearchCitationToolkit,
    evaluate_references,
    extract_cited_indices,
    scan_numeric,
)
from vertical_plugins.research_citation.models import (
    AuditFinding,
    AuditReport,
    CheckStatus,
    EvidenceRef,
    FindingStatus,
    IssueKind,
    ReferenceCheckInput,
    ReferenceCheckResult,
    ReferenceIssue,
)
from vertical_plugins.research_citation.prompts import CITATION_INSTRUCTIONS
from vertical_plugins.research_citation.workflow import (
    CITATION_WORKFLOW_NAME,
    CitationInputState,
    CitationInternalState,
    CitationOutputState,
    InternalState,
    build_citation_workflow,
    build_citation_workflow_contribution,
    evaluate_citations,
    finalize_output,
    graph,
    input_schema,
    output_schema,
    parse_and_validate_input,
)

SKILL_RELATIVE_PATH = "numeric-citation-audit/SKILL.md"
logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


@lru_cache(maxsize=1)
def _load_skill_asset() -> SkillAsset:
    """从包内静态资源读取并缓存 numeric-citation-audit 技能的 SKILL.md 资产。

    :return: 经路径校验的 SkillAsset 实例。
    :rtype: vertical_plugins.common.contracts.SkillAsset
    :author: tongliga
    :date: 2026-10-19
    """
    # 第一步：记录静态资源读取调试日志
    logger.debug("skill_resource_loading")
    # 第二步：利用 importlib.resources 规范安全读取包内技能文档
    skill_text = (
        files("vertical_plugins.research_citation")
        .joinpath("skills", "numeric-citation-audit", "SKILL.md")
        .read_text(encoding="utf-8")
    )
    # 第三步：组装标准 SkillAsset 资产结构
    asset = SkillAsset(relative_path=SKILL_RELATIVE_PATH, content=skill_text)
    logger.debug(
        "skill_resource_loaded", extra={"event_fields": {"skill_path": SKILL_RELATIVE_PATH}}
    )
    return asset


class ResearchCitationPlugin(DomainPlugin):
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
        :type workflow_mode: vertical_plugins.common.contracts.WorkflowMode | None
        :param include_tools: 是否同时导出技能门控工具（纯 Supervisor 模式下可设为 False）。
        :type include_tools: bool
        :return: 无返回值。
        :rtype: None
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：记录插件初始化参数调试日志
        logger.debug(
            "citation_plugin_initialized",
            extra={"event_fields": {"include_tools": include_tools}},
        )
        # 第二步：记录工作流模式与工具导出标志状态
        self._workflow_mode = workflow_mode
        self._include_tools = include_tools

    def build(self, services: BuildServices) -> PluginContribution:
        """返回当前插件的 LangGraph 工作流、原生工具与技能文件声明，无需修改宿主核心逻辑。

        :param services: 宿主在构建期注入的服务实例（如工作区路径与存储对象）。
        :type services: vertical_plugins.common.contracts.BuildServices
        :return: 包含工作流、工具、技能资产与提示指令的声明式 Deep Agents 插件贡献对象。
        :rtype: vertical_plugins.common.contracts.PluginContribution
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：实例化学术引用核查工具包
        toolkit = ResearchCitationToolkit()
        logger.debug("citation_plugin_build_started")
        # 第二步：声明并装配工作流贡献、工具导出、技能文档与引导指令
        return PluginContribution(
            workflows=[build_citation_workflow_contribution(mode=self._workflow_mode)],
            tools=(
                ToolContribution.from_toolkit(toolkit, exposure=Exposure.SKILL)
                if self._include_tools
                else []
            ),
            skills=[_load_skill_asset()],
            instructions=CITATION_INSTRUCTIONS,
        )


__all__ = [
    "CITATION_WORKFLOW_NAME",
    "MAX_CITATION_SPAN",
    "SKILL_RELATIVE_PATH",
    "AuditFinding",
    "AuditReport",
    "CheckStatus",
    "CitationInputState",
    "CitationInternalState",
    "CitationOutputState",
    "EvidenceRef",
    "FindingStatus",
    "InternalState",
    "IssueKind",
    "ReferenceCheckInput",
    "ReferenceCheckResult",
    "ReferenceIssue",
    "ResearchCitationPlugin",
    "ResearchCitationToolkit",
    "build_citation_workflow",
    "build_citation_workflow_contribution",
    "evaluate_citations",
    "evaluate_references",
    "extract_cited_indices",
    "finalize_output",
    "graph",
    "input_schema",
    "output_schema",
    "parse_and_validate_input",
    "scan_numeric",
]
