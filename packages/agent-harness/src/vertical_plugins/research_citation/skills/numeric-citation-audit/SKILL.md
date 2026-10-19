---
name: research-citations-numeric-citation-audit
description: Audit numbered citations such as [1] or [2-4] in research manuscripts using deterministic index checks.
metadata:
  include_tools: research_citations_scan_numeric
---

# Numeric citation audit

1. Obtain the manuscript plaintext and its bibliography in the exact ordering used by the manuscript.
2. Call `research_citations_scan_numeric` on the supplied text and list.
3. Distinguish confirmed reference-number errors from semantic citation validity.
4. Return issue indexes and an actionable checklist. Do not allege scientific misconduct.
5. If the input uses author-date citations, explain that this checker does not apply.
