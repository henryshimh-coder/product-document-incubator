from __future__ import annotations

import importlib
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from src.application.ports.dashboard import ManifestSnapshot
from src.domain.enums import (
    AuthorityLevel,
    ChangeStatus,
    DecisionAction,
    EvidenceSide,
    IssueSeverity,
    IssueStatus,
    KnowledgeStatus,
)
from src.domain.errors import DomainError, ErrorCode
from src.domain.models import (
    BaselineManifest,
    Decision,
    IssueCard,
    IssueEvidence,
    KnowledgeCard,
    Project,
)
from src.infrastructure.db.migrations import migrate
from src.infrastructure.db.repositories import (
    SqliteIssueRepository,
    SqliteKnowledgeRepository,
    SqliteProjectRepository,
)

NOW = datetime(2026, 7, 29, 7, 0, tzinfo=UTC)


def _issue() -> IssueCard:
    return IssueCard(
        id="ISSUE-001",
        project_id="LLD",
        issue_type="information_gap",
        severity=IssueSeverity.PENDING_INFO,
        status=IssueStatus.OPEN,
        title="待会议处理",
        description="需要明确处理结论。",
        evidence=[
            IssueEvidence(
                source_id="BASE-LLD-724_1",
                citation_id="CIT-BASE-001",
                excerpt="当前客群规则。",
                document_version="LLD-724_1",
                page_or_section="目标客群",
                side=EvidenceSide.CURRENT_BASELINE,
            ),
            IssueEvidence(
                source_id="SRC-RISK",
                citation_id="CIT-RISK-001",
                excerpt="风险意见要求收紧客群。",
                document_version="v1.0",
                page_or_section="客群限制",
                side=EvidenceSide.CHALLENGING_SOURCE,
            ),
        ],
        impacted_domains=["产品"],
        options=[],
        ai_recommendation="accept_change",
        ai_confidence=0.7,
        uncertainty="待会议处理",
        owner=None,
        due_at=None,
        created_at=NOW,
        updated_at=NOW,
    )


def _setup(tmp_path):
    dto = importlib.import_module("src.application.dto.decision")
    use_cases = importlib.import_module("src.application.use_cases.record_decision")
    repositories = importlib.import_module("src.infrastructure.db.repositories")
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
    SqliteIssueRepository(db_path).add_many([_issue()])
    knowledge = SqliteKnowledgeRepository(db_path)
    knowledge.upsert_cards(
        [
            KnowledgeCard(
                id=card_id,
                project_id="LLD",
                card_type="rule",
                title=card_id,
                content=content,
                status=KnowledgeStatus.EFFECTIVE,
                product_version="LLD-724_1",
                applicable_scope="演示",
                source_refs=["SRC-BASE:CIT-BASE-001"],
                authority_level=AuthorityLevel.FORMAL_EFFECTIVE,
                owner="产品",
                created_at=NOW,
                updated_at=NOW,
            )
            for card_id, content in (
                ("RULE-001", "当前客群规则。"),
                ("API-CUSTOMER", "客群接口规则。"),
            )
        ]
    )

    class Manifest:
        def read_snapshot(self) -> ManifestSnapshot:
            return ManifestSnapshot(
                BaselineManifest(
                    schema_version="1.0",
                    project_id="LLD",
                    current_baseline_id="BASE-LLD-724_1",
                    current_version="LLD-724_1",
                    parent_baseline_id=None,
                    full_document_path=(
                        "data/obsidian_vault/02_Current_Baseline/LLD-724_1/full.md"
                    ),
                    card_snapshot_path=(
                        "data/obsidian_vault/02_Current_Baseline/LLD-724_1/cards.json"
                    ),
                    full_document_sha256="a" * 64,
                    card_snapshot_sha256="b" * 64,
                    change_request_id=None,
                    approved_by="产品经理",
                    published_at=NOW,
                ),
                "c" * 64,
            )

    use_case = use_cases.RecordDecision(
        issues=SqliteIssueRepository(db_path),
        manifest=Manifest(),
        knowledge=knowledge,
        unit_of_work=repositories.SqliteDecisionUnitOfWork(db_path),
        now=lambda: NOW,
        decision_id_factory=lambda: "DECISION-001",
        change_id_factory=lambda: "CHANGE-001",
    )
    return dto, use_case, db_path


def _change(dto):
    return dto.CreateChangeRequestInput(
        target_card_id="RULE-001",
        before_content="当前客群规则。",
        after_content="收紧后的客群规则。",
        rationale="依据风险意见和会议结论调整。",
        evidence_refs=["CIT-BASE-001", "CIT-RISK-001"],
        impacted_objects=["RULE-001", "API-CUSTOMER"],
        responsible_domain="产品",
        required_approver_role="产品经理",
        demo_confirmer="产品经理",
        target_version="LLD-724_2",
        effective_condition="审批通过且验证完成后发布。",
    )


def test_accept_change_requires_owner_and_verification_condition(tmp_path) -> None:
    """Catches incomplete accept decisions leaking Pydantic errors instead of a stable code."""
    dto, use_case, _ = _setup(tmp_path)
    command = dto.RecordDecisionInput(
        issue_id="ISSUE-001",
        action=DecisionAction.ACCEPT_CHANGE,
        conclusion="采纳风险意见",
        confirmed_by="产品经理",
        responsible_party=None,
        due_at=None,
        verification_condition=None,
        idempotency_key="DECISION-CLICK-001",
        change_request=_change(dto),
    )

    with pytest.raises(DomainError, match="DECISION_FIELDS_REQUIRED"):
        use_case.execute(command)


def test_missing_common_decision_fields_are_reported_by_stable_domain_error(tmp_path) -> None:
    """Catches Pydantic construction errors escaping instead of the governed decision code."""
    dto, use_case, _ = _setup(tmp_path)
    command = dto.RecordDecisionInput(
        issue_id=None,
        action=DecisionAction.KEEP_CURRENT,
        conclusion=None,
        confirmed_by=None,
        idempotency_key=None,
    )

    with pytest.raises(DomainError, match="DECISION_FIELDS_REQUIRED"):
        use_case.execute(command)


@pytest.mark.parametrize(
    ("action", "conclusion", "due_at", "expected_status"),
    [
        (
            DecisionAction.KEEP_CURRENT,
            "维持当前规则，因为正式依据未变化。",
            None,
            IssueStatus.CLOSED,
        ),
        (
            DecisionAction.DEFER,
            "等待补充材料后再处理。",
            NOW + timedelta(days=7),
            IssueStatus.DEFERRED,
        ),
        (
            DecisionAction.FALSE_POSITIVE,
            "判定误报：两段措辞不同但适用范围一致。",
            None,
            IssueStatus.FALSE_POSITIVE,
        ),
    ],
)
def test_non_change_actions_persist_required_reason_or_due_date(
    tmp_path, action, conclusion, due_at, expected_status
) -> None:
    """Catches the three non-change actions failing to update their governed issue state."""
    dto, use_case, db_path = _setup(tmp_path)

    result = use_case.execute(
        dto.RecordDecisionInput(
            issue_id="ISSUE-001",
            action=action,
            conclusion=conclusion,
            confirmed_by="产品经理",
            responsible_party=None,
            due_at=due_at,
            verification_condition=None,
            idempotency_key=f"KEY-{action.value}",
            change_request=None,
        )
    )

    assert result.decision.action == action
    assert result.change_request is None
    assert SqliteIssueRepository(db_path).get("ISSUE-001").status == expected_status


def test_accept_change_is_atomic_and_idempotent_but_ai_recommendation_is_not_an_action(
    tmp_path,
) -> None:
    """Catches duplicate clicks or the displayed AI recommendation becoming a second decision."""
    dto, use_case, db_path = _setup(tmp_path)
    command = dto.RecordDecisionInput(
        issue_id="ISSUE-001",
        action=DecisionAction.ACCEPT_CHANGE,
        conclusion="会议确认采纳风险意见。",
        confirmed_by="产品经理",
        responsible_party="产品负责人",
        due_at=None,
        verification_condition="回归测试通过且审批完成。",
        idempotency_key="DECISION-CLICK-001",
        change_request=_change(dto),
    )

    first = use_case.execute(command)
    second = use_case.execute(command)

    assert second == first
    assert first.change_request is not None
    assert first.change_request.status == ChangeStatus.PENDING_APPROVAL
    assert SqliteIssueRepository(db_path).get("ISSUE-001").status == IssueStatus.DECIDED
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM change_requests").fetchone()[0] == 1


@pytest.mark.parametrize(
    ("updates", "detail"),
    [
        ({"target_card_id": "RULE-MISSING"}, "TARGET_CARD_NOT_FOUND"),
        ({"before_content": "并非当前卡片原文。"}, "BEFORE_CONTENT_MISMATCH"),
        ({"evidence_refs": ["CIT-NOT-IN-ISSUE"]}, "EVIDENCE_NOT_IN_ISSUE"),
        ({"impacted_objects": ["RULE-001", "OBJECT-MISSING"]}, "IMPACT_NOT_FOUND"),
        ({"target_version": "LLD-724_1"}, "TARGET_VERSION_NOT_NEXT"),
        ({"target_version": "LLD-724_3"}, "TARGET_VERSION_NOT_NEXT"),
    ],
)
def test_accept_change_validates_authoritative_target_evidence_impact_and_version(
    tmp_path, updates, detail
) -> None:
    """Catches a syntactically complete but semantically invalid change request."""
    dto, use_case, _ = _setup(tmp_path)
    change = _change(dto).model_copy(update=updates)
    command = dto.RecordDecisionInput(
        issue_id="ISSUE-001",
        action=DecisionAction.ACCEPT_CHANGE,
        conclusion="会议确认采纳风险意见。",
        confirmed_by="产品经理",
        responsible_party="产品负责人",
        verification_condition="回归测试通过且审批完成。",
        idempotency_key=f"INVALID-{detail}",
        change_request=change,
    )

    with pytest.raises(DomainError) as raised:
        use_case.execute(command)

    assert raised.value.code == ErrorCode.CHANGE_FIELDS_REQUIRED.value
    assert raised.value.detail == detail


def test_accept_change_requires_effective_target_from_current_manifest_version(tmp_path) -> None:
    """Catches a candidate or historical card being used as the authoritative before-state."""
    dto, use_case, db_path = _setup(tmp_path)
    current = SqliteKnowledgeRepository(db_path).get_card("RULE-001")
    SqliteKnowledgeRepository(db_path).upsert_cards(
        [
            current.model_copy(
                update={
                    "status": KnowledgeStatus.CANDIDATE,
                    "product_version": "LLD-700_1",
                }
            )
        ]
    )
    command = dto.RecordDecisionInput(
        issue_id="ISSUE-001",
        action=DecisionAction.ACCEPT_CHANGE,
        conclusion="会议确认采纳风险意见。",
        confirmed_by="产品经理",
        responsible_party="产品负责人",
        verification_condition="回归测试通过且审批完成。",
        idempotency_key="INVALID-TARGET-STATE",
        change_request=_change(dto),
    )

    with pytest.raises(DomainError) as raised:
        use_case.execute(command)

    assert raised.value.code == ErrorCode.CHANGE_FIELDS_REQUIRED.value
    assert raised.value.detail == "TARGET_CARD_NOT_CURRENT_EFFECTIVE"


def test_same_idempotency_key_with_different_payload_fails_closed(tmp_path) -> None:
    """Catches a stale result being returned for a materially different second click."""
    dto, use_case, _ = _setup(tmp_path)
    base = dto.RecordDecisionInput(
        issue_id="ISSUE-001",
        action=DecisionAction.KEEP_CURRENT,
        conclusion="维持当前规则，因为正式依据未变化。",
        confirmed_by="产品经理",
        responsible_party=None,
        due_at=None,
        verification_condition=None,
        idempotency_key="SAME-KEY",
        change_request=None,
    )
    use_case.execute(base)

    with pytest.raises(DomainError, match="DECISION_IDEMPOTENCY_CONFLICT"):
        use_case.execute(base.model_copy(update={"conclusion": "另一项会议结论。"}))


@pytest.mark.parametrize(
    "trigger_sql",
    [
        """
        CREATE TRIGGER fail_decision_insert BEFORE INSERT ON decisions
        BEGIN SELECT RAISE(ABORT, 'injected decision failure'); END
        """,
        """
        CREATE TRIGGER fail_issue_update BEFORE UPDATE ON issue_cards
        BEGIN SELECT RAISE(ABORT, 'injected issue failure'); END
        """,
        """
        CREATE TRIGGER fail_change_insert BEFORE INSERT ON change_requests
        BEGIN SELECT RAISE(ABORT, 'injected change failure'); END
        """,
    ],
)
def test_accept_change_maps_sqlite_failures_and_rolls_back_every_write(
    tmp_path, trigger_sql
) -> None:
    """Catches raw SQLite errors or partial state at any transaction write boundary."""
    dto, use_case, db_path = _setup(tmp_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(trigger_sql)
    command = dto.RecordDecisionInput(
        issue_id="ISSUE-001",
        action=DecisionAction.ACCEPT_CHANGE,
        conclusion="会议确认采纳风险意见。",
        confirmed_by="产品经理",
        responsible_party="产品负责人",
        due_at=None,
        verification_condition="回归测试通过且审批完成。",
        idempotency_key="ROLLBACK-KEY",
        change_request=_change(dto),
    )

    with pytest.raises(DomainError) as raised:
        use_case.execute(command)

    assert raised.value.code == ErrorCode.DECISION_PERSISTENCE_FAILED.value
    assert isinstance(raised.value.__cause__, sqlite3.Error)

    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM change_requests").fetchone()[0] == 0
        status = connection.execute(
            "SELECT status FROM issue_cards WHERE id = 'ISSUE-001'"
        ).fetchone()[0]
    assert status == IssueStatus.OPEN.value


def test_decision_uow_maps_connection_open_failure_to_stable_domain_error(tmp_path) -> None:
    """Catches sqlite open/PRAGMA failures escaping before the persistence boundary."""
    repositories = importlib.import_module("src.infrastructure.db.repositories")
    unit_of_work = repositories.SqliteDecisionUnitOfWork(tmp_path)
    decision = Decision(
        id="DECISION-OPEN-FAIL",
        project_id="LLD",
        issue_id="ISSUE-001",
        action=DecisionAction.KEEP_CURRENT,
        conclusion="维持当前规则，因为正式依据未变化。",
        confirmed_by="产品经理",
        responsible_party=None,
        due_at=None,
        verification_condition=None,
        created_at=NOW,
    )

    with pytest.raises(DomainError) as raised:
        unit_of_work.record(
            decision=decision,
            idempotency_key="OPEN-FAIL",
            command_fingerprint="f" * 64,
            issue_status=IssueStatus.CLOSED,
            issue_updated_at=NOW,
            change_request=None,
            relations=[],
        )

    assert raised.value.code == ErrorCode.DECISION_PERSISTENCE_FAILED.value
    assert isinstance(raised.value.__cause__, sqlite3.Error)


def test_decision_uow_cleanup_failures_preserve_original_sqlite_cause(
    monkeypatch,
) -> None:
    """Catches rollback/close errors replacing the SQLite operation that actually failed."""
    repositories = importlib.import_module("src.infrastructure.db.repositories")

    class Connection:
        def execute(self, statement, parameters=()):
            raise sqlite3.OperationalError("primary begin failure")

        def rollback(self):
            raise sqlite3.OperationalError("rollback cleanup failure")

        def close(self):
            raise sqlite3.OperationalError("close cleanup failure")

    monkeypatch.setattr(repositories, "connect", lambda path: Connection())
    unit_of_work = repositories.SqliteDecisionUnitOfWork("ignored.db")
    decision = Decision(
        id="DECISION-CLEANUP-SQLITE",
        project_id="LLD",
        issue_id="ISSUE-001",
        action=DecisionAction.KEEP_CURRENT,
        conclusion="维持当前规则，因为正式依据未变化。",
        confirmed_by="产品经理",
        responsible_party=None,
        due_at=None,
        verification_condition=None,
        created_at=NOW,
    )

    with pytest.raises(DomainError) as raised:
        unit_of_work.record(
            decision=decision,
            idempotency_key="CLEANUP-SQLITE",
            command_fingerprint="a" * 64,
            issue_status=IssueStatus.CLOSED,
            issue_updated_at=NOW,
            change_request=None,
            relations=[],
        )

    assert raised.value.code == ErrorCode.DECISION_PERSISTENCE_FAILED.value
    assert isinstance(raised.value.__cause__, sqlite3.OperationalError)
    assert str(raised.value.__cause__) == "primary begin failure"


def test_decision_uow_cleanup_failures_preserve_stable_domain_error(monkeypatch) -> None:
    """Catches cleanup replacing an ISSUE_NOT_FOUND domain decision."""
    repositories = importlib.import_module("src.infrastructure.db.repositories")

    class EmptyResult:
        def fetchone(self):
            return None

    class Connection:
        def execute(self, statement, parameters=()):
            if statement == "BEGIN IMMEDIATE":
                return EmptyResult()
            return EmptyResult()

        def rollback(self):
            raise sqlite3.OperationalError("rollback cleanup failure")

        def close(self):
            raise sqlite3.OperationalError("close cleanup failure")

    monkeypatch.setattr(repositories, "connect", lambda path: Connection())
    unit_of_work = repositories.SqliteDecisionUnitOfWork("ignored.db")
    decision = Decision(
        id="DECISION-CLEANUP-DOMAIN",
        project_id="LLD",
        issue_id="ISSUE-MISSING",
        action=DecisionAction.KEEP_CURRENT,
        conclusion="维持当前规则，但问题卡已不存在。",
        confirmed_by="产品经理",
        responsible_party=None,
        due_at=None,
        verification_condition=None,
        created_at=NOW,
    )

    with pytest.raises(DomainError) as raised:
        unit_of_work.record(
            decision=decision,
            idempotency_key="CLEANUP-DOMAIN",
            command_fingerprint="b" * 64,
            issue_status=IssueStatus.CLOSED,
            issue_updated_at=NOW,
            change_request=None,
            relations=[],
        )

    assert raised.value.code == ErrorCode.DECISION_INVALID.value
    assert raised.value.detail == "ISSUE_NOT_FOUND"
