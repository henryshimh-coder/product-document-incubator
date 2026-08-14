from __future__ import annotations

import hashlib
import sqlite3
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
    IssueEvidence,
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


def test_project_repository_lists_projects_by_recent_activity_then_id(tmp_path: Path) -> None:
    """Catches project cards appearing in stale or nondeterministic order."""
    db_path = tmp_path / "product_incubator.db"
    migrate(db_path)
    repository = SqliteProjectRepository(db_path)
    projects = [
        Project(
            id="PROJECT_B",
            name="项目 B",
            product_line="说明 B",
            stage="待初始化",
            current_baseline_id=None,
            allow_external_model=False,
            created_at=NOW,
            updated_at=NOW,
        ),
        Project(
            id="PROJECT_A",
            name="项目 A",
            product_line="说明 A",
            stage="待初始化",
            current_baseline_id=None,
            allow_external_model=False,
            created_at=NOW,
            updated_at=NOW,
        ),
        Project(
            id="PROJECT_NEW",
            name="新项目",
            product_line="最近更新",
            stage="待初始化",
            current_baseline_id=None,
            allow_external_model=False,
            created_at=NOW,
            updated_at=NOW + timedelta(minutes=1),
        ),
    ]
    for project in projects:
        repository.add(project)

    assert repository.list_all() == [projects[2], projects[1], projects[0]]


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


def test_source_repository_reads_material_series_and_rejects_duplicate_series_version(
    tmp_path: Path,
) -> None:
    """Catches a version chain accepting two records with the same series version."""
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
    SqliteProjectRepository(db_path).add(project)
    repository = SqliteSourceRepository(db_path)
    first = SourceRecord(
        id="SRC-CHAIN-1",
        project_id="LLD",
        original_filename="需求说明.md",
        archive_path="data/source_archive/LLD/SRC-CHAIN-1/需求说明.md",
        sha256="b" * 64,
        mime_type="text/markdown",
        size_bytes=42,
        source_type="product_requirement",
        authority_level=AuthorityLevel.FORMAL_EFFECTIVE,
        source_department="产品部",
        provider=None,
        document_date=date(2026, 7, 29),
        document_version="v1.0",
        applicable_baseline_version="LLD-724_1",
        security_level=SecurityLevel.L1_PUBLIC_SIMULATED,
        is_redacted=True,
        allow_external_model=True,
        is_sandbox=True,
        ingest_status="completed",
        created_at=NOW,
        material_name="蓝领贷需求说明",
        material_series_id="MAT-LLD-000000000001",
        previous_source_id=None,
    )
    second = first.model_copy(
        update={
            "id": "SRC-CHAIN-2",
            "original_filename": "产品需求终稿.md",
            "archive_path": "data/source_archive/LLD/SRC-CHAIN-2/产品需求终稿.md",
            "sha256": "c" * 64,
            "document_version": "v2.0",
            "previous_source_id": first.id,
            "created_at": NOW + timedelta(minutes=1),
        }
    )
    duplicate_version = second.model_copy(
        update={
            "id": "SRC-CHAIN-DUPLICATE",
            "sha256": "d" * 64,
            "previous_source_id": None,
        }
    )

    repository.add(first)
    repository.add(second)

    assert repository.list_for_series("LLD", first.material_series_id) == [first, second]
    assert repository.find_latest_for_series("LLD", first.material_series_id) == second
    assert repository.find_by_series_version("LLD", first.material_series_id, "v2.0") == second
    with pytest.raises(sqlite3.IntegrityError):
        repository.add(duplicate_version)


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
        raw_severity=IssueSeverity.BLOCKING,
        deterministic_rule_id="GOV-001",
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
    assert stored.raw_severity == IssueSeverity.BLOCKING
    assert stored.deterministic_rule_id == "GOV-001"
    assert stored.created_at == NOW
    assert stored.updated_at == NOW + timedelta(minutes=5)


def test_issue_repository_scopes_fingerprint_uniqueness_to_project(tmp_path: Path) -> None:
    """Catches one project's logical finding overwriting another project's issue."""
    db_path = tmp_path / "product_intelligence.db"
    migrate(db_path)
    projects = SqliteProjectRepository(db_path)
    for project_id in ("LLD", "OTHER"):
        projects.add(
            Project(
                id=project_id,
                name=project_id,
                product_line="轻量交付",
                stage="demo",
                current_baseline_id=None,
                allow_external_model=True,
                created_at=NOW,
                updated_at=NOW,
            )
        )
    base = IssueCard(
        id="ISSUE-LLD",
        project_id="LLD",
        issue_type="stale",
        severity=IssueSeverity.PENDING_INFO,
        status=IssueStatus.OPEN,
        title="版本待核对",
        description="同一逻辑问题可出现在不同项目。",
        evidence=[],
        impacted_domains=["产品"],
        options=[],
        ai_recommendation=None,
        ai_confidence=None,
        uncertainty="待核对",
        fingerprint="a" * 64,
        target_rule_id="RULE-001",
        owner=None,
        due_at=None,
        created_at=NOW,
        updated_at=NOW,
    )
    repository = SqliteIssueRepository(db_path)

    repository.upsert_all([base])
    repository.upsert_all([base.model_copy(update={"id": "ISSUE-OTHER", "project_id": "OTHER"})])

    assert repository.get("ISSUE-LLD").project_id == "LLD"
    assert repository.get("ISSUE-OTHER").project_id == "OTHER"


def test_issue_repository_lists_all_statuses_for_review_history(tmp_path: Path) -> None:
    """Catches decided or closed issues disappearing from application-level filters."""
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
    base = IssueCard(
        id="ISSUE-OPEN",
        project_id="LLD",
        issue_type="information_gap",
        severity=IssueSeverity.PENDING_INFO,
        status=IssueStatus.OPEN,
        title="待处理",
        description="需要补充信息。",
        evidence=[],
        impacted_domains=["产品"],
        options=[],
        ai_recommendation=None,
        ai_confidence=None,
        uncertainty="待处理",
        owner=None,
        due_at=None,
        created_at=NOW,
        updated_at=NOW,
    )
    repository = SqliteIssueRepository(db_path)
    repository.add_many(
        [
            base,
            base.model_copy(
                update={
                    "id": "ISSUE-CLOSED",
                    "status": IssueStatus.CLOSED,
                    "title": "已关闭",
                    "updated_at": NOW + timedelta(minutes=1),
                }
            ),
        ]
    )

    assert {issue.status for issue in repository.list_all("LLD")} == {
        IssueStatus.OPEN,
        IssueStatus.CLOSED,
    }


def test_issue_repository_enriches_one_sided_issue_without_creating_a_new_row(
    tmp_path: Path,
) -> None:
    """Catches evidence supplementation changing logical identity across lint runs."""
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
    baseline = IssueEvidence(
        source_id="BASE-LLD",
        citation_id="CIT-BASE-001",
        excerpt="当前客群规则。",
        document_version="LLD-724_1",
        page_or_section="目标客群",
        side="current_baseline",
    )
    challenge = IssueEvidence(
        source_id="SRC-RISK",
        citation_id="CIT-RISK-001",
        excerpt="风险意见要求收紧。",
        document_version="v1.0",
        page_or_section="客群限制",
        side="challenging_source",
    )
    first = IssueCard(
        id="ISSUE-FIRST",
        project_id="LLD",
        issue_type="conflict",
        severity=IssueSeverity.PENDING_INFO,
        status=IssueStatus.OPEN,
        title="客群依据待补充",
        description="当前只有一侧依据。",
        evidence=[baseline],
        impacted_domains=["产品", "风险"],
        options=[],
        ai_recommendation=None,
        ai_confidence=None,
        uncertainty="缺少对方依据",
        fingerprint="e" * 64,
        target_rule_id="RULE-001",
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
                    "id": "ISSUE-ENRICHED",
                    "severity": IssueSeverity.PENDING_DECISION,
                    "title": "客群边界不一致",
                    "evidence": [baseline, challenge],
                    "uncertainty": "需会议确认",
                    "updated_at": NOW + timedelta(minutes=5),
                }
            )
        ]
    )

    stored = repository.get("ISSUE-FIRST")
    assert stored.severity == IssueSeverity.PENDING_DECISION
    assert stored.evidence == [baseline, challenge]
    with pytest.raises(KeyError):
        repository.get("ISSUE-ENRICHED")


def _db8_fingerprint(issue: IssueCard) -> str:
    normalized = "\n".join(
        (
            issue.issue_type.casefold(),
            "|".join(sorted(item.citation_id for item in issue.evidence)),
            "|".join(sorted(domain.casefold() for domain in issue.impacted_domains)),
            (issue.target_rule_id or "").casefold(),
        )
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@pytest.mark.parametrize("legacy_status", [IssueStatus.OPEN, IssueStatus.CLOSED])
def test_issue_repository_reconciles_unique_db8_fingerprint_and_preserves_state(
    tmp_path: Path,
    legacy_status: IssueStatus,
) -> None:
    """Catches a db8 logical issue becoming a duplicate after the fingerprint redesign."""
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
    legacy = IssueCard(
        id="ISSUE-DB8",
        project_id="LLD",
        issue_type="stale",
        severity=IssueSeverity.PENDING_INFO,
        status=legacy_status,
        title="当前基线引用历史产品规则",
        description="db8 时期的确定性问题。",
        evidence=[],
        impacted_domains=["产品"],
        options=[],
        ai_recommendation=None,
        ai_confidence=None,
        uncertainty="缺少独立挑战依据",
        fingerprint=None,
        target_rule_id="RULE-001",
        owner=None,
        due_at=None,
        created_at=NOW,
        updated_at=NOW,
    )
    legacy = legacy.model_copy(update={"fingerprint": _db8_fingerprint(legacy)})
    with sqlite3.connect(db_path) as connection:
        connection.execute("DROP INDEX idx_issue_project_fingerprint")
        connection.execute(
            "CREATE UNIQUE INDEX idx_issue_fingerprint "
            "ON issue_cards(fingerprint) WHERE fingerprint IS NOT NULL"
        )
    repository = SqliteIssueRepository(db_path)
    repository.add_many([legacy])

    migrate(db_path)
    repository.upsert_all(
        [
            legacy.model_copy(
                update={
                    "id": "ISSUE-NEW",
                    "status": IssueStatus.OPEN,
                    "description": "重新自检后的规则事实。",
                    "raw_severity": IssueSeverity.BLOCKING,
                    "deterministic_rule_id": "VER-001",
                    "fingerprint": "1" * 64,
                    "updated_at": NOW + timedelta(minutes=5),
                }
            )
        ]
    )

    stored = repository.get("ISSUE-DB8")
    assert stored.fingerprint == "1" * 64
    assert stored.status == legacy_status
    assert stored.created_at == NOW
    assert stored.updated_at == NOW + timedelta(minutes=5)
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM issue_cards").fetchone()[0] == 1


def test_legacy_fingerprint_reconciliation_does_not_merge_distinct_rules(
    tmp_path: Path,
) -> None:
    """Catches VER-002 overwriting an old VER-001 row that targets the same card."""
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
    legacy_ver_001 = IssueCard(
        id="ISSUE-VER-001-DB8",
        project_id="LLD",
        issue_type="stale",
        severity=IssueSeverity.PENDING_INFO,
        status=IssueStatus.CLOSED,
        title="当前基线引用历史产品规则",
        description="db8 VER-001。",
        evidence=[],
        impacted_domains=["产品"],
        options=[],
        ai_recommendation=None,
        ai_confidence=None,
        uncertainty="规则事实",
        fingerprint="2" * 64,
        target_rule_id="RULE-001",
        owner=None,
        due_at=None,
        created_at=NOW,
        updated_at=NOW,
    )
    new_ver_002 = legacy_ver_001.model_copy(
        update={
            "id": "ISSUE-VER-002-NEW",
            "status": IssueStatus.OPEN,
            "title": "技术方案对应产品版本落后",
            "description": "VER-002 规则事实。",
            "raw_severity": IssueSeverity.PENDING_DECISION,
            "deterministic_rule_id": "VER-002",
            "fingerprint": "3" * 64,
            "updated_at": NOW + timedelta(minutes=1),
        }
    )
    repository = SqliteIssueRepository(db_path)
    repository.add_many([legacy_ver_001])

    repository.upsert_all([new_ver_002])

    assert repository.get("ISSUE-VER-001-DB8").status == IssueStatus.CLOSED
    assert repository.get("ISSUE-VER-002-NEW").title == "技术方案对应产品版本落后"
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM issue_cards").fetchone()[0] == 2


def test_legacy_fingerprint_reconciliation_rejects_semantic_impersonation(
    tmp_path: Path,
) -> None:
    """Catches a semantic issue taking over an old deterministic row by copied title/target."""
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
    legacy = IssueCard(
        id="ISSUE-LEGACY-DETERMINISTIC",
        project_id="LLD",
        issue_type="stale",
        severity=IssueSeverity.PENDING_INFO,
        status=IssueStatus.CLOSED,
        title="当前基线引用历史产品规则",
        description="db8 确定性问题。",
        evidence=[],
        impacted_domains=["产品"],
        options=[],
        ai_recommendation=None,
        ai_confidence=None,
        uncertainty="缺少独立挑战依据",
        fingerprint=None,
        target_rule_id="RULE-001",
        owner=None,
        due_at=None,
        created_at=NOW,
        updated_at=NOW,
    )
    legacy = legacy.model_copy(update={"fingerprint": _db8_fingerprint(legacy)})
    semantic = legacy.model_copy(
        update={
            "id": "ISSUE-SEMANTIC",
            "status": IssueStatus.OPEN,
            "description": "AI 产生的同名语义问题。",
            "fingerprint": "4" * 64,
            "deterministic_rule_id": None,
            "raw_severity": None,
            "updated_at": NOW + timedelta(minutes=1),
        }
    )
    repository = SqliteIssueRepository(db_path)
    repository.add_many([legacy])

    repository.upsert_all([semantic])

    assert repository.get("ISSUE-LEGACY-DETERMINISTIC").status == IssueStatus.CLOSED
    assert repository.get("ISSUE-SEMANTIC").description.startswith("AI ")
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM issue_cards").fetchone()[0] == 2


def test_issue_upsert_acquires_immediate_transaction_before_identity_lookup(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Catches two writers racing between fingerprint lookup and insert."""
    repositories = __import__(
        "src.infrastructure.db.repositories", fromlist=["SqliteIssueRepository"]
    )
    real_connect = __import__("src.infrastructure.db.connection", fromlist=["connect"]).connect
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
    statements: list[str] = []

    def traced_connect(path):
        connection = real_connect(path)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(repositories, "connect", traced_connect)
    issue = IssueCard(
        id="ISSUE-ATOMIC",
        project_id="LLD",
        issue_type="insufficient_evidence",
        severity=IssueSeverity.PENDING_INFO,
        status=IssueStatus.OPEN,
        title="缺少来源",
        description="需补充来源。",
        evidence=[],
        impacted_domains=["产品"],
        options=[],
        ai_recommendation=None,
        ai_confidence=None,
        uncertainty="需补充来源",
        raw_severity=IssueSeverity.PENDING_INFO,
        deterministic_rule_id="STR-001",
        fingerprint="5" * 64,
        target_rule_id="RULE-001",
        owner=None,
        due_at=None,
        created_at=NOW,
        updated_at=NOW,
    )

    repositories.SqliteIssueRepository(db_path).upsert_all([issue])

    normalized = [statement.strip().upper() for statement in statements]
    begin_index = normalized.index("BEGIN IMMEDIATE")
    select_index = next(
        index
        for index, statement in enumerate(normalized)
        if statement.startswith("SELECT ID, CREATED_AT FROM ISSUE_CARDS")
    )
    assert begin_index < select_index
