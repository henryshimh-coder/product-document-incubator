from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from src.domain.enums import EvidenceSide, IssueSeverity, KnowledgeStatus
from src.domain.models import Baseline, IssueEvidence, KnowledgeCard


class DeterministicFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_id: str
    issue_type: str
    severity: IssueSeverity
    title: str
    description: str
    evidence: list[IssueEvidence]
    impacted_domains: Annotated[list[str], Field(min_length=1)]
    uncertainty: str | None = None
    target_rule_id: str | None = None


def run_rule(
    rule_id: str,
    *,
    card: KnowledgeCard,
    baseline: Baseline,
) -> DeterministicFinding | None:
    """Run one card-scoped deterministic rule without any model dependency."""

    if card.project_id != baseline.project_id:
        return None
    if rule_id == "GOV-001":
        if card.status == KnowledgeStatus.EFFECTIVE:
            return None
        source_ref = card.source_refs[0] if card.source_refs else card.id
        source_id = source_ref.partition(":")[0]
        return DeterministicFinding(
            rule_id=rule_id,
            issue_type="conflict",
            severity=IssueSeverity.BLOCKING,
            title="非生效内容进入当前基线",
            description=f"卡片 {card.id} 的状态为 {card.status.value}，不得进入当前基线。",
            evidence=[
                IssueEvidence(
                    source_id=baseline.id,
                    citation_id=f"CIT-{baseline.id}",
                    excerpt=f"当前基线版本为 {baseline.version}。",
                    document_version=baseline.version,
                    page_or_section="Baseline Manifest",
                    side=EvidenceSide.CURRENT_BASELINE,
                ),
                IssueEvidence(
                    source_id=source_id,
                    citation_id=source_ref,
                    excerpt=card.content,
                    document_version=card.product_version,
                    page_or_section=card.title,
                    side=EvidenceSide.CHALLENGING_SOURCE,
                ),
            ],
            impacted_domains=[card.owner],
            target_rule_id=card.id,
        )
    if rule_id == "STR-001" and not card.source_refs:
        return _information_finding(rule_id, card, "知识卡缺少来源")
    if rule_id == "VER-001" and card.product_version != baseline.version:
        return _major_finding(
            rule_id,
            card,
            baseline,
            "当前基线引用历史产品规则",
            severity=IssueSeverity.BLOCKING,
        )
    if (
        rule_id == "VER-002"
        and card.card_type in {"technical", "technical_solution"}
        and card.product_version != baseline.version
    ):
        return _major_finding(
            rule_id,
            card,
            baseline,
            "技术方案对应产品版本落后",
            severity=IssueSeverity.PENDING_DECISION,
        )
    if rule_id == "MKT-001" and card.card_type == "market_judgment" and not card.source_refs:
        return _information_finding(rule_id, card, "市场判断没有证据或验证计划")
    return None


def _information_finding(
    rule_id: str,
    card: KnowledgeCard,
    title: str,
    *,
    severity: IssueSeverity = IssueSeverity.PENDING_INFO,
) -> DeterministicFinding:
    return DeterministicFinding(
        rule_id=rule_id,
        issue_type="insufficient_evidence" if severity == IssueSeverity.PENDING_INFO else "stale",
        severity=severity,
        title=title,
        description=f"卡片 {card.id} 未通过 {rule_id} 确定性检查。",
        evidence=[],
        impacted_domains=[card.owner],
        uncertainty=title,
        target_rule_id=card.id,
    )


def _major_finding(
    rule_id: str,
    card: KnowledgeCard,
    baseline: Baseline,
    title: str,
    *,
    severity: IssueSeverity,
) -> DeterministicFinding:
    source_ref = card.source_refs[0] if card.source_refs else f"CARD-{card.id}"
    source_id = source_ref.partition(":")[0]
    if source_id == baseline.id:
        source_id = f"CARD-{card.id}"
    return DeterministicFinding(
        rule_id=rule_id,
        issue_type="stale",
        severity=severity,
        title=title,
        description=f"卡片 {card.id} 的版本 {card.product_version} 与 {baseline.version} 不一致。",
        evidence=[
            IssueEvidence(
                source_id=baseline.id,
                citation_id=f"CIT-{baseline.id}",
                excerpt=f"当前基线版本为 {baseline.version}。",
                document_version=baseline.version,
                page_or_section="Baseline Manifest",
                side=EvidenceSide.CURRENT_BASELINE,
            ),
            IssueEvidence(
                source_id=source_id,
                citation_id=source_ref,
                excerpt=card.content,
                document_version=card.product_version,
                page_or_section=card.title,
                side=EvidenceSide.CHALLENGING_SOURCE,
            ),
        ],
        impacted_domains=[card.owner],
        uncertainty=None,
        target_rule_id=card.id,
    )


def finding_timestamp() -> datetime:
    return datetime.now(UTC)
