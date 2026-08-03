from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.domain.enums import (
    AuthorityLevel,
    BaselineStatus,
    ChangeReviewAction,
    ChangeStatus,
    DecisionAction,
    IssueSeverity,
    IssueStatus,
    KnowledgeStatus,
    SecurityLevel,
)
from src.domain.models import (
    Baseline,
    ChangeRequest,
    Decision,
    IssueCard,
    KnowledgeCard,
    Project,
    SourceRecord,
)
from src.infrastructure.db.migrations import migrate
from src.infrastructure.db.repositories import (
    SqliteBaselineRepository,
    SqliteChangeRepository,
    SqliteDecisionRepository,
    SqliteIssueRepository,
    SqliteKnowledgeRepository,
    SqliteProjectRepository,
    SqliteSourceRepository,
)

NOW = datetime(2026, 7, 29, 7, 0, tzinfo=UTC)


def test_source_repository_round_trip_and_hash_lookup(tmp_path: Path) -> None:
    """Protects duplicate-source detection and exact SourceRecord restoration."""
    db_path = tmp_path / "product_intelligence.db"
    migrate(db_path)
    project = Project(
        id="LLD",
        name="产品智策",
        product_line="轻量交付",
        stage="demo",
        current_baseline_id=None,
        allow_external_model=True,
        created_at=NOW,
        updated_at=NOW,
    )
    source = SourceRecord(
        id="SRC-001",
        project_id="LLD",
        original_filename="产品规则.md",
        archive_path="data/source_archive/LLD/SRC-001/产品规则.md",
        sha256="a" * 64,
        mime_type="text/markdown",
        size_bytes=42,
        source_type="formal_document",
        authority_level=AuthorityLevel.FORMAL_EFFECTIVE,
        source_department="产品部",
        provider=None,
        document_date=date(2026, 7, 29),
        document_version="1.0",
        applicable_baseline_version="LLD-724_1",
        security_level=SecurityLevel.L1_PUBLIC_SIMULATED,
        is_redacted=True,
        allow_external_model=True,
        is_sandbox=True,
        ingest_status="completed",
        created_at=NOW,
    )

    SqliteProjectRepository(db_path).add(project)
    repository = SqliteSourceRepository(db_path)
    repository.add(source)

    assert repository.get(source.id) == source
    assert repository.find_by_sha256(source.project_id, source.sha256) == source
    assert repository.list_for_project(source.project_id) == [source]


def test_baseline_repository_round_trip_and_supersede(tmp_path: Path) -> None:
    """Protects baseline history from losing its immutable release metadata."""
    db_path = tmp_path / "product_intelligence.db"
    migrate(db_path)
    SqliteProjectRepository(db_path).add(
        Project(
            id="LLD",
            name="产品智策",
            product_line="轻量交付",
            stage="demo",
            current_baseline_id=None,
            allow_external_model=False,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    baseline = Baseline(
        id="BASE-LLD-724_1",
        project_id="LLD",
        version="LLD-724_1",
        parent_baseline_id=None,
        status=BaselineStatus.EFFECTIVE,
        full_document_path="data/obsidian_vault/02_Current_Baseline/LLD-724_1/full.md",
        card_snapshot_path="data/obsidian_vault/02_Current_Baseline/LLD-724_1/cards.json",
        manifest_sha256="c" * 64,
        change_request_id=None,
        approved_by="产品经理",
        effective_at=NOW,
        created_at=NOW,
    )
    repository = SqliteBaselineRepository(db_path)
    repository.add(baseline)

    assert repository.get(baseline.id) == baseline
    assert repository.get_by_version("LLD", "LLD-724_1") == baseline
    assert repository.list_for_project("LLD") == [baseline]
    repository.mark_superseded(baseline.id)
    assert repository.get(baseline.id) == baseline.model_copy(
        update={"status": BaselineStatus.SUPERSEDED}
    )


def test_knowledge_issue_decision_and_change_repositories_restore_validated_models(
    tmp_path: Path,
) -> None:
    """Protects JSON field round trips and review audit persistence."""
    db_path = tmp_path / "product_intelligence.db"
    migrate(db_path)
    SqliteProjectRepository(db_path).add(
        Project(
            id="LLD",
            name="产品智策",
            product_line="轻量交付",
            stage="demo",
            current_baseline_id=None,
            allow_external_model=True,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    card = KnowledgeCard(
        id="RULE-001",
        project_id="LLD",
        card_type="rule",
        title="目标客群",
        content="脱敏后的当前规则",
        status=KnowledgeStatus.EFFECTIVE,
        product_version="LLD-724_1",
        applicable_scope="演示",
        source_refs=["SRC-BASE"],
        authority_level=AuthorityLevel.FORMAL_EFFECTIVE,
        owner="产品经理",
        confidence=0.9,
        created_at=NOW,
        updated_at=NOW,
    )
    issue = IssueCard(
        id="ISSUE-001",
        project_id="LLD",
        issue_type="information_gap",
        severity=IssueSeverity.PENDING_INFO,
        status=IssueStatus.OPEN,
        title="缺少市场依据",
        description="需要补充市场证据。",
        evidence=[],
        impacted_domains=["市场"],
        options=[{"action": "补充材料"}],
        ai_recommendation=None,
        ai_confidence=None,
        uncertainty="尚未提供市场资料",
        owner=None,
        due_at=None,
        created_at=NOW,
        updated_at=NOW,
    )
    decision = Decision(
        id="DECISION-001",
        project_id="LLD",
        issue_id=issue.id,
        action=DecisionAction.KEEP_CURRENT,
        conclusion="当前材料不足，暂时维持现行规则。",
        confirmed_by="产品经理",
        responsible_party=None,
        due_at=None,
        verification_condition=None,
        created_at=NOW,
    )
    change = ChangeRequest(
        id="CHANGE-001",
        project_id="LLD",
        issue_id=issue.id,
        decision_id=decision.id,
        target_card_id=card.id,
        before_content="旧规则",
        after_content="新规则",
        rationale="依据会议结论更新规则。",
        evidence_refs=["SRC-BASE"],
        impacted_objects=[card.id],
        responsible_domain="产品",
        required_approver_role="产品经理",
        demo_confirmer="产品经理",
        status=ChangeStatus.PENDING_APPROVAL,
        review_action=None,
        reviewed_by=None,
        review_comment=None,
        review_idempotency_key=None,
        reviewed_at=None,
        target_version="LLD-724_2",
        effective_condition="审批通过后发布。",
        created_at=NOW,
        updated_at=NOW,
    )

    knowledge = SqliteKnowledgeRepository(db_path)
    issues = SqliteIssueRepository(db_path)
    decisions = SqliteDecisionRepository(db_path)
    changes = SqliteChangeRepository(db_path)
    knowledge.upsert_cards([card])
    issues.add_many([issue])
    decisions.add(decision, idempotency_key="decision-001")
    changes.add(change)
    reviewed = changes.record_review(
        change_id=change.id,
        action=ChangeReviewAction.APPROVE,
        reviewed_by="产品经理",
        comment="已检查修改前后、依据、影响对象和目标版本。",
        idempotency_key="review-001",
        reviewed_at=NOW,
        target_status=ChangeStatus.APPROVED,
    )

    assert knowledge.list_effective("LLD", "LLD-724_1") == [card]
    assert issues.list_open("LLD") == [issue]
    assert decisions.get(decision.id) == decision
    assert reviewed.status == ChangeStatus.APPROVED
    assert changes.get(change.id) == reviewed
    assert changes.find_by_review_idempotency_key("review-001") == reviewed
    assert changes.list_pending("LLD") == []

    issue_updated_at = NOW + timedelta(minutes=1)
    change_updated_at = NOW + timedelta(minutes=2)
    issues.update_status(issue.id, IssueStatus.DECIDED, issue_updated_at)
    changes.update_status(change.id, ChangeStatus.PUBLISHED, change_updated_at)

    assert issues.get(issue.id).updated_at == issue_updated_at
    assert changes.get(change.id).updated_at == change_updated_at

    invalid_timestamps = (
        datetime(2026, 7, 29, 8, 0),
        datetime(2026, 7, 29, 8, 0, tzinfo=timezone(timedelta(hours=8))),
    )
    for invalid_timestamp in invalid_timestamps:
        with pytest.raises(ValueError, match="UTC"):
            issues.update_status(issue.id, IssueStatus.CLOSED, invalid_timestamp)
        with pytest.raises(ValueError, match="UTC"):
            changes.update_status(change.id, ChangeStatus.PUBLISHED, invalid_timestamp)

    assert issues.get(issue.id).updated_at == issue_updated_at
    assert changes.get(change.id).updated_at == change_updated_at


def test_knowledge_repository_lists_only_version_scoped_candidate_and_conflict_notices(
    tmp_path: Path,
) -> None:
    """Catches effective, rejected, or cross-version cards leaking into query notices."""
    db_path = tmp_path / "product_intelligence.db"
    migrate(db_path)
    SqliteProjectRepository(db_path).add(
        Project(
            id="LLD",
            name="产品智策",
            product_line="轻量交付",
            stage="demo",
            current_baseline_id=None,
            allow_external_model=True,
            created_at=NOW,
            updated_at=NOW,
        )
    )

    def card(card_id: str, status: KnowledgeStatus, version: str = "LLD-724_1"):
        return KnowledgeCard(
            id=card_id,
            project_id="LLD",
            card_type="rule",
            title=card_id,
            content=f"{card_id} 内容",
            status=status,
            product_version=version,
            applicable_scope="演示",
            source_refs=["SRC-BASE"],
            authority_level=AuthorityLevel.FORMAL_EFFECTIVE,
            owner="产品经理",
            created_at=NOW,
            updated_at=NOW,
        )

    repository = SqliteKnowledgeRepository(db_path)
    repository.upsert_cards(
        [
            card("RULE-EFFECTIVE", KnowledgeStatus.EFFECTIVE),
            card("RULE-CANDIDATE", KnowledgeStatus.CANDIDATE),
            card("RULE-CONFLICT", KnowledgeStatus.CONFLICT),
            card("RULE-REJECTED", KnowledgeStatus.REJECTED),
            card("RULE-OLD-CANDIDATE", KnowledgeStatus.CANDIDATE, "LLD-700_1"),
        ]
    )

    assert [item.id for item in repository.list_notices("LLD", "LLD-724_1")] == [
        "RULE-CANDIDATE",
        "RULE-CONFLICT",
    ]


def test_issue_repository_upserts_repeated_lint_fingerprint_without_losing_creation_time(
    tmp_path: Path,
) -> None:
    """Catches a second lint run crashing or creating a duplicate for the same fingerprint."""
    db_path = tmp_path / "product_intelligence.db"
    migrate(db_path)
    SqliteProjectRepository(db_path).add(
        Project(
            id="LLD",
            name="产品智策",
            product_line="轻量交付",
            stage="demo",
            current_baseline_id=None,
            allow_external_model=True,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    first = IssueCard(
        id="ISSUE-FIRST",
        project_id="LLD",
        issue_type="information_gap",
        severity=IssueSeverity.PENDING_INFO,
        status=IssueStatus.OPEN,
        title="首次发现",
        description="需要补充市场依据。",
        evidence=[],
        impacted_domains=["市场"],
        options=[],
        ai_recommendation=None,
        ai_confidence=None,
        uncertainty="缺少市场依据",
        fingerprint="f" * 64,
        owner=None,
        due_at=None,
        created_at=NOW,
        updated_at=NOW,
    )
    repository = SqliteIssueRepository(db_path)
    repository.upsert_all([first])
    repository.upsert_all(
        [
            first.model_copy(
                update={
                    "id": "ISSUE-SECOND",
                    "title": "再次发现",
                    "updated_at": NOW + timedelta(minutes=5),
                }
            )
        ]
    )

    stored = repository.get("ISSUE-FIRST")
    assert stored.title == "再次发现"
    assert stored.created_at == NOW
    assert stored.updated_at == NOW + timedelta(minutes=5)
