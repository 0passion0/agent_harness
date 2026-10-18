"""作为 LangChain 工具与 BaseToolkit 暴露的确定性学术引用检查实现模块。"""

from __future__ import annotations

import logging
from time import perf_counter

from langchain_core.tools import BaseTool, BaseToolkit, tool

from research_citation.checker.evaluator import evaluate_references
from research_citation.models.check import ReferenceCheckInput

logger = logging.getLogger(__name__)


@tool("research_citations_scan_numeric", args_schema=ReferenceCheckInput)
def scan_numeric(manuscript_text: str, bibliography: list[str]) -> str:
    """根据参考文献列表长度校验手稿中的数字引用序号。

    本工具仅对数字引用标号进行范围与覆盖率验证，不验证引用的论文是否存在或是否支持相应学术观点。

    :param manuscript_text: 包含诸如 [1]、[1, 2] 或 [1-3] 形式数字引用的手稿正文纯文本。
    :param bibliography: 按照手稿引用顺序排列的参考文献条目列表。
    :return: 经过 Pydantic 校验并序列化为 JSON 字符串的引用序号异常比对结果。
    """
    started = perf_counter()
    logger.debug(
        "citation_scan_started", extra={"event_fields": {"bibliography_count": len(bibliography)}}
    )
    try:
        result = evaluate_references(manuscript_text, bibliography)
    except Exception as exc:
        logger.error(
            "citation_scan_failed", extra={"event_fields": {"error_type": type(exc).__name__}}
        )
        raise
    logger.info(
        "citation_scan_completed",
        extra={
            "event_fields": {
                "status": result.status,
                "citation_count": len(result.cited_indices),
                "bibliography_count": len(bibliography),
                "issue_count": len(result.issues),
                "duration_ms": round((perf_counter() - started) * 1000, 2),
            }
        },
    )
    return result.model_dump_json()


scan_numeric.handle_validation_error = (
    "Invalid citation input: check size/schema and use citation IDs of at most 9 digits."
)


class ResearchCitationToolkit(BaseToolkit):
    """遵循 LangChain 标准 BaseToolkit 规范的学术引用核查工具包。"""

    def get_tools(self) -> list[BaseTool]:
        """导出当前工具包内的所有 LangChain 原生工具实例。

        :return: 包含 scan_numeric 在内的 BaseTool 列表。
        """
        return [scan_numeric]


__all__ = [
    "ResearchCitationToolkit",
    "scan_numeric",
]
