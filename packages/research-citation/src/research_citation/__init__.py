"""Research Citation 学术引用审计插件包公共导出模块。

架构子包划分：
- `research_citation.models`: 引用核查输入/输出契约与证据驱动的学术审计报告 Pydantic 模型
- `research_citation.checker`: 纯函数式引用解析器、确定性比对评估器与 LangChain `BaseToolkit` 工具集
- `research_citation.workflow`: 遵循三层状态契约（`InternalState` / `input_schema` / `output_schema`）的确定性 LangGraph 工作流
- `research_citation.plugin`: 遵循 `vertical_harness` 领域插件协议的入口点实现
"""

from logging import NullHandler, getLogger

from research_citation.checker import (
    MAX_CITATION_SPAN,
    ResearchCitationToolkit,
    evaluate_references,
    extract_cited_indices,
    scan_numeric,
)
from research_citation.models import (
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
from research_citation.plugin import ResearchCitationPlugin
from research_citation.workflow import (
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

getLogger(__name__).addHandler(NullHandler())

__all__ = [
    "CITATION_WORKFLOW_NAME",
    "MAX_CITATION_SPAN",
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
