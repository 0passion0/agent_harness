"""确定性引用序号范围与文献表覆盖率评估器模块。"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from research_citation.checker.parser import extract_cited_indices
from research_citation.models.check import (
    CheckStatus,
    IssueKind,
    ReferenceCheckResult,
    ReferenceIssue,
)

logger = logging.getLogger(__name__)


def evaluate_references(
    manuscript_text: str,
    bibliography: Sequence[str],
) -> ReferenceCheckResult:
    """对提取的正文引用序号与参考文献列表执行确定性集合比对。

    :param manuscript_text: 手稿正文纯文本。
    :param bibliography: 按顺序排列的参考文献条目序列。
    :return: 结构化的 ReferenceCheckResult 校验结果模型。
    """
    logger.debug(
        "citation_evaluation_started",
        extra={"event_fields": {"bibliography_count": len(bibliography)}},
    )
    cited_indices = extract_cited_indices(manuscript_text)
    if not cited_indices:
        logger.debug(
            "citation_evaluation_completed",
            extra={"event_fields": {"status": CheckStatus.NOT_APPLICABLE, "issue_count": 0}},
        )
        return ReferenceCheckResult(
            status=CheckStatus.NOT_APPLICABLE,
            cited_indices=[],
            issues=[],
        )

    valid_range = range(1, len(bibliography) + 1)
    cited_set = set(cited_indices)

    # 1. 查找超出参考文献列表长度的非法越界引用
    out_of_range_issues = [
        ReferenceIssue(
            kind=IssueKind.OUT_OF_RANGE,
            index=idx,
            explanation="No bibliography entry at this index",
        )
        for idx in cited_indices
        if idx not in valid_range
    ]

    # 2. 查找参考文献列表中存在但在手稿正文中从未被引用的孤立文献条目
    uncited_issues = [
        ReferenceIssue(
            kind=IssueKind.UNCITED,
            index=idx,
            explanation="Bibliography entry has no matching citation",
        )
        for idx in valid_range
        if idx not in cited_set
    ]

    issues = [*out_of_range_issues, *uncited_issues]
    logger.debug(
        "citation_evaluation_completed",
        extra={
            "event_fields": {
                "issue_count": len(issues),
                "status": CheckStatus.ISSUES if issues else CheckStatus.PASSED,
            }
        },
    )
    return ReferenceCheckResult(
        status=CheckStatus.ISSUES if issues else CheckStatus.PASSED,
        cited_indices=cited_indices,
        issues=issues,
    )


__all__ = [
    "evaluate_references",
]
