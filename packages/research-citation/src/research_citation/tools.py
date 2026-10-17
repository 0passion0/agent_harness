"""Deterministic citation checks exposed as LangChain tools."""

import re

from langchain.tools import tool

from research_citation.schemas import ReferenceCheckInput, ReferenceCheckResult, ReferenceIssue


_BRACKET_PATTERN = re.compile(r"\[([0-9,\s\-]+)\]")


@tool("research_citations_scan_numeric", args_schema=ReferenceCheckInput)
def scan_numeric(manuscript_text: str, bibliography: list[str]) -> str:
    """Verify numeric in-text citations against the bibliography length.

    This does not verify whether a cited paper exists or supports a claim.

    :param manuscript_text: Plain manuscript text using [1], [1, 2], or [1-3].
    :param bibliography: Reference entries in manuscript ordering.
    :return: Pydantic-validated JSON with citation-index inconsistencies.
    """
    cited = set()
    for match in _BRACKET_PATTERN.finditer(manuscript_text):
        content = match.group(1)
        for part in content.split(","):
            part = part.strip()
            if "-" in part:
                pieces = [s.strip() for s in part.split("-")]
                if len(pieces) != 2 or not all(s.isdecimal() for s in pieces):
                    continue
                start, end = map(int, pieces)
                if 0 < start <= end and end - start <= 1000:
                    cited.update(range(start, end + 1))
            elif part.isdecimal():
                cited.add(int(part))
    if not cited:
        return ReferenceCheckResult(status="not_applicable", cited_indices=[], issues=[]).model_dump_json()
    issues = [
        ReferenceIssue(kind="out_of_range", index=i, explanation="No bibliography entry at this index")
        for i in sorted(cited)
        if i < 1 or i > len(bibliography)
    ]
    issues.extend(
        ReferenceIssue(kind="uncited", index=i, explanation="Bibliography entry has no matching citation")
        for i in range(1, len(bibliography) + 1)
        if i not in cited
    )
    return ReferenceCheckResult(
        status="issues" if issues else "passed",
        cited_indices=sorted(cited),
        issues=issues,
    ).model_dump_json()
