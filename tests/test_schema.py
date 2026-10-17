"""Contract tests for reference input and tool output."""

import pytest
from pydantic import ValidationError

from research_citation.schemas import ReferenceCheckInput, ReferenceCheckResult
from research_citation.tools import scan_numeric
from vertical_harness.contracts import PluginManifest, SkillAsset


def test_schema_rejects_extras():
    """Invalid caller payload must fail closed."""
    with pytest.raises(ValidationError):
        ReferenceCheckInput(manuscript_text="[1]", bibliography=["Paper"], unexpected=True)


def test_reference_mismatch():
    """Detect unbound indexes without asserting paper legitimacy."""
    text = scan_numeric.invoke({"manuscript_text": "Source [1] and [3].", "bibliography": ["Paper A", "Paper B"]})
    result = ReferenceCheckResult.model_validate_json(text)
    assert result.status == "issues"
    assert {(i.kind, i.index) for i in result.issues} == {("out_of_range", 3), ("uncited", 2)}


def test_plugin_contract():
    """Reject malformed plugin manifests and skill traversal."""
    with pytest.raises(ValidationError):
        PluginManifest(plugin_id="../../evil", version="0.1.0", description="Malformed plugin identifier")
    with pytest.raises(ValidationError):
        SkillAsset(relative_path="../hack/SKILL.md", content="x")


def test_verified_finding_requires_evidence():
    """Business-specific evidence constraints are stronger than JSON syntax."""
    from research_citation.report import AuditFinding

    with pytest.raises(ValidationError):
        AuditFinding(claim="Citation claim", status="verified", evidence=[])
