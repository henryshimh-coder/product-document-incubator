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
