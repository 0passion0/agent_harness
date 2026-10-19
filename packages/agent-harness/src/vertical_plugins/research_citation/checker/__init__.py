"""确定性引用解析、核查评估与 LangChain 工具集子包公共导出。"""

from vertical_plugins.research_citation.checker.evaluator import evaluate_references
from vertical_plugins.research_citation.checker.parser import (
    MAX_CITATION_SPAN,
    extract_cited_indices,
)
from vertical_plugins.research_citation.checker.tools import ResearchCitationToolkit, scan_numeric

__all__ = [
    "MAX_CITATION_SPAN",
    "ResearchCitationToolkit",
    "evaluate_references",
    "extract_cited_indices",
    "scan_numeric",
]
