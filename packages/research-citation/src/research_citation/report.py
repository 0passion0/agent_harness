"""Research audit artifact schema with explicit evidence uncertainty."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EvidenceRef(BaseModel):
    """Traceable immutable source location."""

    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(min_length=1)
    page: int = Field(ge=1)
    source_sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    excerpt: str = Field(min_length=1)


class AuditFinding(BaseModel):
    """Evidence-backed finding, never a determination of author intent."""

    model_config = ConfigDict(extra="forbid")

    claim: str = Field(min_length=1)
    status: Literal["verified", "suspected", "insufficient_evidence"]
    evidence: list[EvidenceRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_evidence_for_verification(self):
        """Prevent validated findings without a traceable source."""
        if self.status == "verified" and not self.evidence:
            raise ValueError("Verified findings require at least one evidence reference")
        return self


class AuditReport(BaseModel):
    """Deliverable schema for research audits."""

    model_config = ConfigDict(extra="forbid")

    manuscript_id: str = Field(min_length=1)
    findings: list[AuditFinding] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
