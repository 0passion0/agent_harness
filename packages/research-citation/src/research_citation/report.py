"""具有明确证据不确定性的研究审计产物模型模式定义模块。"""

from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Literal


class EvidenceRef(BaseModel):
    """可追溯且不可变的证据源位置定义模型。"""

    # 禁止模型包含未声明的多余字段
    model_config = ConfigDict(extra="forbid")

    # 文档唯一标识符（如 DOI 或内部编号）
    document_id: str = Field(min_length=1)
    # 证据所在的页码，必须大于等于 1
    page: int = Field(ge=1)
    # 原始文档的 SHA-256 哈希值，确保内容防篡改与精准对应
    source_sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    # 从原始文献中抽取的支持证据原文摘录
    excerpt: str = Field(min_length=1)


class AuditFinding(BaseModel):
    """有证据支撑的审计结论模型（严禁主观推测作者意图）。"""

    # 禁止模型包含未声明的多余字段
    model_config = ConfigDict(extra="forbid")

    # 待校验的核心论点或引用主张
    claim: str = Field(min_length=1)
    # 审计核实状态：verified（已核实）、suspected（存疑）、insufficient_evidence（证据不足）
    status: Literal["verified", "suspected", "insufficient_evidence"]
    # 支撑该发现的证据引用列表
    evidence: list[EvidenceRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_evidence_for_verification(self):
        """防止在缺乏可追溯证据源的情况下生成已核实的审计结论。

        :return: 验证通过后的当前 AuditFinding 实例。
        :raises ValueError: 当结论状态为 'verified'（已核实）却未提供任何证据时抛出。
        """
        # 如果结论被标记为 verified，则必须至少包含一条来源确凿的引用证据
        if self.status == "verified" and not self.evidence:
            raise ValueError("Verified findings require at least one evidence reference")
        return self


class AuditReport(BaseModel):
    """学术审计交付报告的数据模式。"""

    # 禁止模型包含未声明的多余字段
    model_config = ConfigDict(extra="forbid")

    # 接受审计的手稿唯一标识符
    manuscript_id: str = Field(min_length=1)
    # 审计发现与问题列表
    findings: list[AuditFinding] = Field(default_factory=list)
    # 审计过程中客观存在的局限性说明（如某些封闭文献无法获取）
    limitations: list[str] = Field(default_factory=list)
