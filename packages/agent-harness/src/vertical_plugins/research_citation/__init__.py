"""Research Citation 学术引用审计内置插件：入口类、清单与装配入口。

本文件是插件根目录唯一的 Python 文件；解析算法、工具、数据模型与工作流实现均位于子包：
- `vertical_plugins.research_citation.models`: 引用核查输入/输出契约与证据驱动的学术审计报告 Pydantic 模型
- `vertical_plugins.research_citation.checker`: 纯函数式引用解析器与确定性比对评估器
- `vertical_plugins.research_citation.tools`: LangChain `BaseToolkit` 工具集封装
- `vertical_plugins.research_citation.workflow`: 继承 `BaseWorkflow` 的确定性三阶段 LangGraph 工作流
- `vertical_plugins.research_citation.skills`: 随插件分发的技能资产
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
from vertical_plugins.research_citation.checker import (
    MAX_CITATION_SPAN,
    evaluate_references,
    extract_cited_indices,
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
from vertical_plugins.research_citation.tools import (
    ResearchCitationToolkit,
    scan_numeric,
)
from vertical_plugins.research_citation.workflow import (
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

SKILL_RELATIVE_PATH = "numeric-citation-audit/SKILL.md"
CITATION_INSTRUCTIONS = (
    "When a task matches numeric citation checking, read the 'numeric-citation-audit' skill "
    "and request user authorization before executing the 'citation_audit' workflow. "
    "Scope is strictly limited to bracketed numeric citation indices, ranges, and bibliography consistency."
)

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class ResearchCitationPlugin(WorkflowPlugin):
    """学术引用审计工作流插件；继承 `WorkflowPlugin` 统一管理技能发现与工作流构建。"""

    manifest = PluginManifest(
        plugin_id="research.citations",
        api_version=1,
        version="0.1.0",
        description="Deterministic in-text reference checks for research manuscripts",
    )
    workflow_cls = CitationAuditWorkflow
    instructions_template = CITATION_INSTRUCTIONS
    expose_tools_by_default = False

    def __init__(
        self,
        *,
        workflow_mode: WorkflowMode | None = None,
        include_tools: bool = False,
    ) -> None:
        """初始化引用审计插件。

        :param workflow_mode: 可选的工作流接入模式覆盖（compiled 或 async）。
        :type workflow_mode: vertical_plugins.common.contracts.WorkflowMode | None
        :param include_tools: 是否显式导出底层工具集，默认 False（初始化阶段仅暴露 Skill 与受控工作流请求）。
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
        """按需构造学术引用核查工具包贡献。

        :param services: 宿主构建服务。
        :type services: vertical_plugins.common.contracts.BuildServices
        :return: 工具贡献元组。
        :rtype: tuple[vertical_plugins.common.contracts.ToolContribution, ...]
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：将 ResearchCitationToolkit 封装为技能级暴露工具
        _ = services
        return tuple(
            ToolContribution.from_toolkit(ResearchCitationToolkit(), exposure=Exposure.SKILL)
        )

    def build(self, services: BuildServices) -> PluginContribution:
        """统一组装学术引用核查插件的工作流、技能与系统指令贡献。

        :param services: 宿主在构建期注入的服务实例。
        :type services: vertical_plugins.common.contracts.BuildServices
        :return: 插件贡献对象。
        :rtype: vertical_plugins.common.contracts.PluginContribution
        :author: tongliga
        :date: 2026-10-19
        """
        # 第一步：通过基类创建绑定上下文的 CitationAuditWorkflow 实例
        workflow = self.create_workflow(services)
        skills = self.discover_skills()
        tools = (
            self.build_tools(services)
            if (self._include_tools or self.expose_tools_by_default)
            else ()
        )
        instructions = self.build_instructions(services)

        # 第二步：组装 PluginContribution
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
    # 第一步：延迟构建默认引用审计状态图
    if name == "graph":
        return get_citation_graph()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "CITATION_INSTRUCTIONS",
    "CITATION_WORKFLOW_NAME",
    "MAX_CITATION_SPAN",
    "SKILL_RELATIVE_PATH",
    "AuditFinding",
    "AuditReport",
    "CheckStatus",
    "CitationAuditWorkflow",
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
    "evaluate_references",
    "extract_cited_indices",
    "get_citation_graph",
    "input_schema",
    "output_schema",
    "scan_numeric",
]
