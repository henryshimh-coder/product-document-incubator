from __future__ import annotations

import importlib
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from src.domain.enums import (
    AuthorityLevel,
    ChangeReviewAction,
    ChangeStatus,
    EvidenceSide,
    IssueSeverity,
    IssueStatus,
    KnowledgeStatus,
    SecurityLevel,
)

NOW = datetime(2026, 7, 29, tzinfo=UTC)
SHA_A = "a" * 64
SHA_B = "b" * 64


def _models():
    return importlib.import_module("src.domain.models")


def _pending_change_request(models):
    return models.ChangeRequest(
        id="CHG-LLD-001",
        project_id="LLD",
        issue_id="ISSUE-LLD-001",
        decision_id="DEC-LLD-001",
        target_card_id="RULE-LLD-001",
        before_content="原规则",
        after_content="新规则",
        rationale="采纳风险意见",
        evidence_refs=["CIT-RISK-001"],
        impacted_objects=["产品方案/目标客群"],
        responsible_domain="产品",
        required_approver_role="产品与风险负责人",
        demo_confirmer="产品经理",
        status=ChangeStatus.PENDING_APPROVAL,
        review_action=None,
        reviewed_by=None,
        review_comment=None,
        review_idempotency_key=None,
        reviewed_at=None,
        target_version="LLD-724_2",
        effective_condition="发布后生效",
        created_at=NOW,
        updated_at=NOW,
    )


def test_project_rejects_blank_business_id():
    """Catches persisting an entity that cannot be addressed by repositories or audit logs."""
    models = _models()

    with pytest.raises(ValidationError, match="id"):
        models.Project(
            id=" ",
            name="推荐官链客计划",
            product_line="零售信贷",
            stage="方案评审",
            current_baseline_id=None,
            allow_external_model=False,
            created_at=NOW,
            updated_at=NOW,
        )


def test_source_record_rejects_non_sha256_digest():
    """Catches accepting a malformed digest that would break duplicate detection."""
    models = _models()

    with pytest.raises(ValidationError, match="sha256"):
        models.SourceRecord(
            id="SRC-LLD-001",
            project_id="LLD",
            original_filename="风险意见.md",
            archive_path="data/source_archive/not-a-digest.md",
            sha256="not-a-sha256",
            mime_type="text/markdown",
            size_bytes=120,
            source_type="risk_opinion",
            authority_level=AuthorityLevel.PROFESSIONAL_OPINION,
            source_department="风险",
            provider=None,
            document_date=date(2026, 7, 29),
            document_version="v1.0",
            applicable_baseline_version="LLD-724_1",
            security_level=SecurityLevel.L2_INTERNAL,
            is_redacted=True,
            allow_external_model=True,
            is_sandbox=False,
            ingest_status="pending",
            created_at=NOW,
        )


def test_source_record_allows_zero_size_consistently_with_storage_schema():
    """Catches a domain/database constraint mismatch during repository hydration."""
    models = _models()

    record = models.SourceRecord(
        id="SRC-LLD-EMPTY",
        project_id="LLD",
        original_filename="空白材料.md",
        archive_path="data/source_archive/" + "a" * 64 + ".md",
        sha256="a" * 64,
        mime_type="text/markdown",
        size_bytes=0,
        source_type="other",
        authority_level=AuthorityLevel.DISCUSSION_REFERENCE,
        source_department="产品",
        provider=None,
        document_date=date(2026, 7, 29),
        document_version="v1.0",
        applicable_baseline_version="LLD-724_1",
        security_level=SecurityLevel.L1_PUBLIC_SIMULATED,
        is_redacted=True,
        allow_external_model=False,
        is_sandbox=True,
        ingest_status="pending",
        created_at=NOW,
    )

    assert record.size_bytes == 0


def test_effective_knowledge_card_requires_source_reference():
    """Catches publishing an effective rule that cannot be traced to any source."""
    models = _models()

    with pytest.raises(ValidationError, match="source_refs"):
        models.KnowledgeCard(
            id="RULE-LLD-001",
            project_id="LLD",
            card_type="product_rule",
            title="目标客群",
            content="当前生效规则",
            status=KnowledgeStatus.EFFECTIVE,
            product_version="LLD-724_1",
            applicable_scope="一期",
            source_refs=[],
            authority_level=AuthorityLevel.FORMAL_EFFECTIVE,
            owner="产品",
            confidence=None,
            created_at=NOW,
            updated_at=NOW,
        )


def test_major_issue_requires_two_distinct_evidence_items():
    """Catches presenting a blocking conflict without evidence from both sides."""
    models = _models()
    evidence = models.IssueEvidence(
        source_id="SRC-BASE",
        citation_id="CIT-BASE-001",
        excerpt="当前方案原文",
        document_version="LLD-724_1",
        page_or_section="目标客群",
        side=EvidenceSide.CURRENT_BASELINE,
    )

    with pytest.raises(ValidationError, match="evidence"):
        models.IssueCard(
            id="ISSUE-LLD-001",
            project_id="LLD",
            issue_type="conflict",
            severity=IssueSeverity.BLOCKING,
            status=IssueStatus.OPEN,
            title="客群边界不一致",
            description="需要会议确认当前执行口径",
            evidence=[evidence, evidence],
            impacted_domains=["产品", "风险"],
            options=[],
            ai_recommendation=None,
            ai_confidence=None,
            uncertainty=None,
            owner=None,
            due_at=None,
            created_at=NOW,
            updated_at=NOW,
        )


def test_major_issue_requires_evidence_from_two_distinct_sources():
    """Catches two excerpts from one side being presented as a two-sided conflict."""
    models = _models()
    evidence = [
        models.IssueEvidence(
            source_id="SRC-RISK",
            citation_id=f"CIT-RISK-{index}",
            excerpt=f"风险意见片段 {index}",
            document_version="v1.0",
            page_or_section=f"第 {index} 节",
            side=(EvidenceSide.CURRENT_BASELINE if index == 1 else EvidenceSide.CHALLENGING_SOURCE),
        )
        for index in (1, 2)
    ]

    with pytest.raises(ValidationError, match="distinct sources"):
        models.IssueCard(
            id="ISSUE-LLD-001",
            project_id="LLD",
            issue_type="conflict",
            severity=IssueSeverity.BLOCKING,
            status=IssueStatus.OPEN,
            title="客群边界不一致",
            description="需要会议确认当前执行口径",
            evidence=evidence,
            impacted_domains=["产品", "风险"],
            options=[],
            ai_recommendation=None,
            ai_confidence=None,
            uncertainty=None,
            owner=None,
            due_at=None,
            created_at=NOW,
            updated_at=NOW,
        )


def test_major_issue_requires_evidence_from_both_sides():
    """Catches two different sources from the same side being presented as a conflict."""
    models = _models()
    evidence = [
        models.IssueEvidence(
            source_id=f"SRC-RISK-{index}",
            citation_id=f"CIT-RISK-{index}",
            excerpt=f"风险意见片段 {index}",
            document_version="v1.0",
            page_or_section=f"第 {index} 节",
            side=EvidenceSide.CHALLENGING_SOURCE,
        )
        for index in (1, 2)
    ]

    with pytest.raises(ValidationError, match="both evidence sides"):
        models.IssueCard(
            id="ISSUE-LLD-003",
            project_id="LLD",
            issue_type="conflict",
            severity=IssueSeverity.PENDING_DECISION,
            status=IssueStatus.OPEN,
            title="风险意见存在分歧",
            description="两份风险意见均建议调整，但没有当前基线侧依据",
            evidence=evidence,
            impacted_domains=["产品", "风险"],
            options=[],
            ai_recommendation=None,
            ai_confidence=None,
            uncertainty=None,
            owner=None,
            due_at=None,
            created_at=NOW,
            updated_at=NOW,
        )


def test_pending_information_issue_must_describe_missing_content():
    """Catches an information gap that does not say what evidence is missing."""
    models = _models()

    with pytest.raises(ValidationError, match="uncertainty"):
        models.IssueCard(
            id="ISSUE-LLD-002",
            project_id="LLD",
            issue_type="missing_market_evidence",
            severity=IssueSeverity.PENDING_INFO,
            status=IssueStatus.OPEN,
            title="缺少同业证据",
            description="当前材料中没有同业对标数据",
            evidence=[],
            impacted_domains=["市场"],
            options=[],
            ai_recommendation=None,
            ai_confidence=None,
            uncertainty=None,
            owner=None,
            due_at=None,
            created_at=NOW,
            updated_at=NOW,
        )


def test_failed_direct_status_assignment_does_not_mutate_change_request():
    """Catches a failed validation leaving an unreviewed change in approved state."""
    models = _models()
    change = _pending_change_request(models)

    with pytest.raises(ValidationError):
        change.status = ChangeStatus.APPROVED

    assert change.status == ChangeStatus.PENDING_APPROVAL


def test_change_transition_returns_a_new_fully_validated_instance():
    """Catches in-place mutation or unchecked copies bypassing approval audit fields."""
    models = _models()
    transition = importlib.import_module("src.domain.policies.state_transition")
    pending = _pending_change_request(models)

    approved = transition.transition_change(
        pending,
        ChangeStatus.APPROVED,
        review_action=ChangeReviewAction.APPROVE,
        reviewed_by="产品经理",
        review_comment="已核对修改前后、证据、影响对象和目标版本。",
        review_idempotency_key="REVIEW-CHG-001",
        reviewed_at=NOW,
        updated_at=NOW,
    )

    assert pending.status == ChangeStatus.PENDING_APPROVAL
    assert approved.status == ChangeStatus.APPROVED
    assert approved.review_action == ChangeReviewAction.APPROVE


def test_publish_transition_preserves_existing_approval_audit():
    """Catches the publish transition erasing the review record it depends on."""
    models = _models()
    transition = importlib.import_module("src.domain.policies.state_transition")
    pending = _pending_change_request(models)
    approved = transition.transition_change(
        pending,
        ChangeStatus.APPROVED,
        review_action=ChangeReviewAction.APPROVE,
        reviewed_by="产品经理",
        review_comment="已核对修改前后、证据、影响对象和目标版本。",
        review_idempotency_key="REVIEW-CHG-001",
        reviewed_at=NOW,
        updated_at=NOW,
    )

    published = transition.transition_change(
        approved,
        ChangeStatus.PUBLISHED,
        updated_at=NOW,
    )

    assert published.status == ChangeStatus.PUBLISHED
    assert published.review_action == ChangeReviewAction.APPROVE
    assert published.reviewed_by == "产品经理"


def test_approved_change_requires_complete_review_audit():
    """Catches an approved state without reviewer, comment, idempotency key, and time."""
    models = _models()

    with pytest.raises(ValidationError, match="review"):
        models.ChangeRequest(
            id="CHG-LLD-001",
            project_id="LLD",
            issue_id="ISSUE-LLD-001",
            decision_id="DEC-LLD-001",
            target_card_id="RULE-LLD-001",
            before_content="原规则",
            after_content="新规则",
            rationale="采纳风险意见",
            evidence_refs=["CIT-RISK-001"],
            impacted_objects=["产品方案/目标客群"],
            responsible_domain="产品",
            required_approver_role="产品与风险负责人",
            demo_confirmer="产品经理",
            status=ChangeStatus.APPROVED,
            review_action=ChangeReviewAction.APPROVE,
            reviewed_by=None,
            review_comment=None,
            review_idempotency_key=None,
            reviewed_at=None,
            target_version="LLD-724_2",
            effective_condition="发布后生效",
            created_at=NOW,
            updated_at=NOW,
        )


@pytest.mark.parametrize("review_comment", ["已批", "批" * 201])
def test_review_comment_must_be_between_10_and_200_characters(review_comment):
    """Catches an unauditable or oversized approval comment entering reviewed state."""
    models = _models()

    with pytest.raises(ValidationError, match="review_comment"):
        models.ChangeRequest(
            **{
                **_pending_change_request(models).model_dump(),
                "status": ChangeStatus.APPROVED,
                "review_action": ChangeReviewAction.APPROVE,
                "reviewed_by": "产品经理",
                "review_comment": review_comment,
                "review_idempotency_key": "REVIEW-CHG-001",
                "reviewed_at": NOW,
            }
        )


@pytest.mark.parametrize(
    ("action", "responsible_party", "due_at", "verification_condition"),
    [
        ("accept_change", None, None, "风险复核通过"),
        ("accept_change", "产品经理", None, None),
        ("defer", "产品经理", None, "材料补齐后复核"),
    ],
)
def test_decision_action_requires_its_execution_fields(
    action,
    responsible_party,
    due_at,
    verification_condition,
):
    """Catches persisting a decision that cannot be assigned, verified, or revisited."""
    models = _models()

    with pytest.raises(ValidationError, match="decision"):
        models.Decision(
            id="DEC-LLD-001",
            project_id="LLD",
            issue_id="ISSUE-LLD-001",
            action=action,
            conclusion="会议同意按讨论方案执行",
            confirmed_by="产品经理",
            responsible_party=responsible_party,
            due_at=due_at,
            verification_condition=verification_condition,
            created_at=NOW,
        )


def test_false_positive_decision_requires_an_explanatory_conclusion():
    """Catches marking an issue false positive without recording an auditable reason."""
    models = _models()

    with pytest.raises(ValidationError, match="decision"):
        models.Decision(
            id="DEC-LLD-002",
            project_id="LLD",
            issue_id="ISSUE-LLD-002",
            action="false_positive",
            conclusion="误报",
            confirmed_by="产品经理",
            responsible_party=None,
            due_at=None,
            verification_condition=None,
            created_at=NOW,
        )


def test_schema_relation_types_construct_valid_relations():
    """Catches schema configuration drifting from the relation contract used by the domain."""
    models = _models()
    schema = yaml.safe_load(Path("config/schema.yaml").read_text(encoding="utf-8"))

    for relation_type in schema["relation_types"]:
        relation = models.Relation(
            id=f"REL-{relation_type}",
            project_id="LLD",
            source_id="SRC-001",
            relation_type=relation_type,
            target_id="CARD-001",
            source_ref=None,
            created_at=NOW,
        )
        assert relation.relation_type == relation_type


def test_schema_evidence_sides_match_domain_enum():
    """Catches workflow configuration drifting from the two-sided evidence contract."""
    schema = yaml.safe_load(Path("config/schema.yaml").read_text(encoding="utf-8"))

    assert schema["evidence_sides"] == [side.value for side in EvidenceSide]


def test_baseline_manifest_rejects_invalid_content_hash():
    """Catches a manifest that cannot prove the integrity of its formal files."""
    models = _models()

    with pytest.raises(ValidationError, match="full_document_sha256"):
        models.BaselineManifest(
            schema_version="1.0",
            project_id="LLD",
            current_baseline_id="BASE-LLD-724_1",
            current_version="LLD-724_1",
            parent_baseline_id=None,
            full_document_path=("data/obsidian_vault/02_Current_Baseline/LLD-724_1/full.md"),
            card_snapshot_path=("data/obsidian_vault/02_Current_Baseline/LLD-724_1/cards.json"),
            full_document_sha256="invalid",
            card_snapshot_sha256=SHA_B,
            change_request_id=None,
            approved_by="产品经理",
            published_at=NOW,
        )


def test_model_call_log_requires_correlation_id_and_accepts_workflow_run_id():
    """Catches model calls that cannot be correlated with their Dify workflow run."""
    models = _models()

    call = models.ModelCallLog(
        id="CALL-001",
        project_id="LLD",
        task_type="query",
        workflow_run_id="WF-001",
        correlation_id="CORR-001",
        source_ids=["SRC-001"],
        baseline_version="LLD-724_1",
        model_label="dify-query",
        prompt_version="query-v1",
        schema_version="1.0",
        authorized=True,
        redacted=True,
        outbound_chars=120,
        outbound_coverage=0.2,
        result_mode="realtime",
        status="succeeded",
        started_at=NOW,
        finished_at=NOW,
        elapsed_ms=12,
        error_code=None,
    )

    assert call.correlation_id == "CORR-001"
    assert call.workflow_run_id == "WF-001"


def test_event_log_requires_top_level_correlation_id():
    """Catches events whose correlation ID is buried in unqueryable payload data."""
    models = _models()

    event = models.EventLog(
        id="EVENT-001",
        project_id="LLD",
        event_type="model_call_completed",
        entity_type="model_call",
        entity_id="CALL-001",
        actor="system",
        correlation_id="CORR-001",
        payload={"status": "succeeded"},
        created_at=NOW,
    )

    assert event.correlation_id == "CORR-001"
