from __future__ import annotations

import importlib
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from src.domain.enums import (
    ChangeStatus,
    DecisionAction,
    IssueSeverity,
    IssueStatus,
)
from src.domain.errors import DomainError
from src.domain.models import IssueCard, Project
from src.infrastructure.db.migrations import migrate
from src.infrastructure.db.repositories import SqliteIssueRepository, SqliteProjectRepository

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
        evidence=[],
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
    use_case = use_cases.RecordDecision(
        issues=SqliteIssueRepository(db_path),
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


def test_accept_change_rolls_back_decision_issue_and_change_on_any_write_failure(tmp_path) -> None:
    """Catches a partial decision commit when ChangeRequest persistence fails."""
    dto, use_case, db_path = _setup(tmp_path)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_change_insert BEFORE INSERT ON change_requests
            BEGIN SELECT RAISE(ABORT, 'injected failure'); END
            """
        )
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

    with pytest.raises(sqlite3.Error, match="injected failure"):
        use_case.execute(command)

    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM change_requests").fetchone()[0] == 0
        status = connection.execute(
            "SELECT status FROM issue_cards WHERE id = 'ISSUE-001'"
        ).fetchone()[0]
    assert status == IssueStatus.OPEN.value
