from __future__ import annotations

import sqlite3
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.application.dto.release import ReviewChangeRequestInput
from src.domain.enums import ChangeReviewAction, ChangeStatus
from src.domain.errors import DomainError, ErrorCode
from tests.integration.release_env import (
    NOW,
    REVIEW_COMMENT,
    REVIEWER,
    build_release_environment,
)


def _use_case(env):
    from src.application.use_cases.review_change_request import ReviewChangeRequest
    from src.infrastructure.db.repositories import SqliteReviewUnitOfWork

    return ReviewChangeRequest(
        changes=env.changes,
        unit_of_work=SqliteReviewUnitOfWork(env.db_path, event_logger=env.event_logger),
        now=lambda: NOW,
        event_id_factory=lambda: f"EVENT-{uuid4().hex.upper()}",
    )


def _command(action: ChangeReviewAction, key: str = "REVIEW-KEY-1") -> ReviewChangeRequestInput:
    return ReviewChangeRequestInput(
        change_request_id="CHANGE-001",
        action=action,
        reviewed_by=REVIEWER,
        comment=REVIEW_COMMENT,
        idempotency_key=key,
    )


@pytest.mark.parametrize(
    ("action", "expected_status"),
    [
        (ChangeReviewAction.APPROVE, ChangeStatus.APPROVED),
        (ChangeReviewAction.REJECT, ChangeStatus.REJECTED),
        (ChangeReviewAction.DEFER, ChangeStatus.DEFERRED),
        (ChangeReviewAction.REQUEST_INFO, ChangeStatus.NEEDS_INFO),
    ],
)
def test_review_change_maps_action_to_status(tmp_path, action, expected_status) -> None:
    """Catches a review action writing the wrong governed status or duplicating writes."""
    env = build_release_environment(tmp_path)
    use_case = _use_case(env)
    command = _command(action)

    first = use_case.execute(command)
    second = use_case.execute(command)

    assert first.status == expected_status
    assert first.review_action == action
    assert first.reviewed_by == REVIEWER
    assert first.review_comment == REVIEW_COMMENT
    assert first.reviewed_at == NOW
    assert second.id == first.id
    assert second.status == expected_status
    with sqlite3.connect(env.db_path) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM event_logs WHERE event_type = 'change_reviewed'"
            ).fetchone()[0]
            == 1
        )


def test_same_idempotency_key_with_different_command_fails_closed(tmp_path) -> None:
    """Catches a stale review result being returned for a materially different command."""
    env = build_release_environment(tmp_path)
    use_case = _use_case(env)
    use_case.execute(_command(ChangeReviewAction.APPROVE, key="SAME-KEY"))

    with pytest.raises(DomainError) as raised:
        use_case.execute(
            _command(ChangeReviewAction.REJECT, key="SAME-KEY").model_copy(
                update={"comment": "另一份完全不同的复核意见。"}
            )
        )

    assert raised.value.code == ErrorCode.REVIEW_IDEMPOTENCY_CONFLICT.value


def test_same_idempotency_key_with_different_action_fails_closed(tmp_path) -> None:
    """Catches the same key being reused for a different review action."""
    env = build_release_environment(tmp_path)
    use_case = _use_case(env)
    use_case.execute(_command(ChangeReviewAction.APPROVE, key="SAME-KEY"))

    with pytest.raises(DomainError, match="REVIEW_IDEMPOTENCY_CONFLICT"):
        use_case.execute(_command(ChangeReviewAction.DEFER, key="SAME-KEY"))


@pytest.mark.parametrize(
    "status",
    [
        ChangeStatus.APPROVED,
        ChangeStatus.REJECTED,
        ChangeStatus.DEFERRED,
        ChangeStatus.NEEDS_INFO,
        ChangeStatus.PUBLISHED,
    ],
)
def test_non_pending_change_is_not_reviewable(tmp_path, status) -> None:
    """Catches a second review bypassing the state machine on an already reviewed change."""
    from tests.integration.release_env import make_change

    env = build_release_environment(tmp_path, change=make_change(status))
    use_case = _use_case(env)

    with pytest.raises(DomainError, match="CHANGE_NOT_REVIEWABLE"):
        use_case.execute(_command(ChangeReviewAction.APPROVE, key="NEW-KEY"))


def test_missing_change_is_not_reviewable(tmp_path) -> None:
    """Catches reviewing a change request that does not exist."""
    env = build_release_environment(tmp_path)
    use_case = _use_case(env)

    with pytest.raises(DomainError, match="CHANGE_NOT_REVIEWABLE"):
        use_case.execute(
            _command(ChangeReviewAction.APPROVE).model_copy(
                update={"change_request_id": "CHANGE-MISSING"}
            )
        )


def test_review_uow_rereads_status_inside_transaction(tmp_path) -> None:
    """Catches a concurrent review slipping through between pre-check and transaction."""
    from src.infrastructure.db.repositories import SqliteReviewUnitOfWork
    from tests.integration.release_env import make_change

    env = build_release_environment(
        tmp_path, change=make_change(ChangeStatus.APPROVED, idempotency_key="OTHER-KEY")
    )
    unit_of_work = SqliteReviewUnitOfWork(env.db_path, event_logger=env.event_logger)

    with pytest.raises(DomainError, match="CHANGE_NOT_REVIEWABLE"):
        unit_of_work.record_review(
            change_id="CHANGE-001",
            action=ChangeReviewAction.REJECT,
            reviewed_by=REVIEWER,
            comment=REVIEW_COMMENT,
            idempotency_key="RACE-KEY",
            reviewed_at=NOW,
            expected_status=ChangeStatus.PENDING_APPROVAL,
            target_status=ChangeStatus.REJECTED,
            event=_review_event(),
        )

    assert env.changes.get("CHANGE-001").status == ChangeStatus.APPROVED
    with sqlite3.connect(env.db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM event_logs").fetchone()[0] == 0


def test_review_sqlite_failure_maps_to_stable_error_and_rolls_back(tmp_path) -> None:
    """Catches raw SQLite errors or partial review state at the transaction boundary."""
    env = build_release_environment(tmp_path)
    with sqlite3.connect(env.db_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER fail_review_update BEFORE UPDATE ON change_requests
            BEGIN SELECT RAISE(ABORT, 'injected review failure'); END
            """
        )
    use_case = _use_case(env)

    with pytest.raises(DomainError) as raised:
        use_case.execute(_command(ChangeReviewAction.APPROVE))

    assert raised.value.code == ErrorCode.REVIEW_PERSISTENCE_FAILED.value
    assert isinstance(raised.value.__cause__, sqlite3.Error)
    assert env.changes.get("CHANGE-001").status == ChangeStatus.PENDING_APPROVAL
    with sqlite3.connect(env.db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM event_logs").fetchone()[0] == 0


def test_review_jsonl_append_failure_keeps_committed_sqlite_fact(tmp_path) -> None:
    """Catches a JSONL audit failure rolling back an already committed human review."""
    env = build_release_environment(tmp_path)
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    env.event_logger.log_path = blocker / "app.log.jsonl"
    use_case = _use_case(env)

    reviewed = use_case.execute(_command(ChangeReviewAction.APPROVE))

    assert reviewed.status == ChangeStatus.APPROVED
    assert env.changes.get("CHANGE-001").status == ChangeStatus.APPROVED
    blocker.unlink()
    blocker.mkdir()
    env.event_logger.reconcile()
    lines = (blocker / "app.log.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert '"change_reviewed"' in lines[0]


def test_review_input_rejects_invalid_comment_and_extra_fields() -> None:
    """Catches an out-of-range comment or unexpected field passing the input contract."""
    with pytest.raises(ValidationError):
        ReviewChangeRequestInput(
            change_request_id="CHANGE-001",
            action=ChangeReviewAction.APPROVE,
            reviewed_by=REVIEWER,
            comment="过短",
            idempotency_key="KEY-1",
        )
    with pytest.raises(ValidationError):
        ReviewChangeRequestInput(
            change_request_id="CHANGE-001",
            action=ChangeReviewAction.APPROVE,
            reviewed_by=REVIEWER,
            comment=REVIEW_COMMENT,
            idempotency_key="KEY-1",
            unexpected="field",
        )


def _review_event():
    from src.domain.models import EventLog

    return EventLog(
        id="EVENT-RACE",
        project_id="LLD",
        event_type="change_reviewed",
        entity_type="change_request",
        entity_id="CHANGE-001",
        actor=REVIEWER,
        correlation_id="REVIEW-CHANGE-001",
        payload={"action": "reject", "comment": REVIEW_COMMENT},
        created_at=NOW,
    )
