"""确定性引用解析与核查评估子包公共导出。"""

from vertical_plugins.research_citation.checker.evaluator import evaluate_references
from vertical_plugins.research_citation.checker.parser import (
    MAX_CITATION_SPAN,
    extract_cited_indices,
)
from vertical_plugins.research_citation.tools.toolkit import (
    ResearchCitationToolkit,
    scan_numeric,
)

__all__ = [
    "MAX_CITATION_SPAN",
    "ResearchCitationToolkit",
    "evaluate_references",
    "extract_cited_indices",
    "scan_numeric",
]
