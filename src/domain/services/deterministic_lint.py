from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from src.domain.enums import IssueSeverity, KnowledgeStatus
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


class DeterministicRuleFacts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    unauthorized_model_call: bool = False
    change_mapping_exists: bool | None = None
    cost_recalculation_exists: bool | None = None


def run_rule(
    rule_id: str,
    *,
    card: KnowledgeCard,
    baseline: Baseline,
    known_source_ids: set[str] | None = None,
    facts: DeterministicRuleFacts | None = None,
) -> DeterministicFinding | None:
    """Run one card-scoped deterministic rule without any model dependency."""

    if card.project_id != baseline.project_id:
        return None
    if rule_id == "GOV-001":
        if card.status == KnowledgeStatus.EFFECTIVE:
            return None
        return _information_finding(
            rule_id,
            card,
            "非生效内容进入当前基线",
            severity=IssueSeverity.BLOCKING,
            issue_type="conflict",
            description=f"卡片 {card.id} 的状态为 {card.status.value}，不得进入当前基线。",
        )
    if rule_id == "STR-001" and not card.source_refs:
        return _information_finding(rule_id, card, "知识卡缺少来源")
    if rule_id == "STR-002" and known_source_ids is not None:
        missing = [ref for ref in card.source_refs if ref.partition(":")[0] not in known_source_ids]
        if missing:
            return _information_finding(
                rule_id,
                card,
                "当前卡片引用不存在",
                description=f"卡片 {card.id} 引用的来源不存在：{', '.join(missing)}。",
            )
    if rule_id == "GOV-002" and facts is not None and facts.unauthorized_model_call:
        return _information_finding(
            rule_id,
            card,
            "未授权资料禁止外部模型调用",
            severity=IssueSeverity.BLOCKING,
            issue_type="insufficient_evidence",
            description="已根据项目与资料安全属性确认本次外调未获授权。",
        )
    if (
        rule_id == "GOV-003"
        and card.card_type
        in {
            "formal_decision",
            "meeting_decision",
        }
        and facts is not None
    ):
        if facts.change_mapping_exists is False:
            return _information_finding(
                rule_id,
                card,
                "正式会议决定未映射到产品变更",
                severity=IssueSeverity.PENDING_DECISION,
                issue_type="not_synchronized",
            )
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
    if (
        rule_id == "COST-001"
        and card.card_type == "cost_parameter_change"
        and facts is not None
        and facts.cost_recalculation_exists is False
    ):
        return _information_finding(
            rule_id,
            card,
            "影响成本的产品参数变化后未重算",
            severity=IssueSeverity.PENDING_DECISION,
            issue_type="not_synchronized",
        )
    return None


def _information_finding(
    rule_id: str,
    card: KnowledgeCard,
    title: str,
    *,
    severity: IssueSeverity = IssueSeverity.PENDING_INFO,
    issue_type: str | None = None,
    description: str | None = None,
) -> DeterministicFinding:
    return DeterministicFinding(
        rule_id=rule_id,
        issue_type=issue_type
        or ("insufficient_evidence" if severity == IssueSeverity.PENDING_INFO else "stale"),
        severity=severity,
        title=title,
        description=description or f"卡片 {card.id} 未通过 {rule_id} 确定性检查。",
        evidence=[],
        impacted_domains=[card.owner],
        uncertainty=(
            "该结果是基线快照规则事实，缺少独立挑战依据"
            if rule_id in {"GOV-001", "VER-001", "VER-002"}
            else title
        ),
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
    return _information_finding(
        rule_id,
        card,
        title,
        severity=severity,
        issue_type="stale",
        description=(
            f"卡片 {card.id} 的版本 {card.product_version} 与 {baseline.version} 不一致。"
        ),
    )


def finding_timestamp() -> datetime:
    return datetime.now(UTC)
