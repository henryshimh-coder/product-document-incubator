from __future__ import annotations

from datetime import datetime

from src.domain.enums import ChangeReviewAction, ChangeStatus
from src.domain.errors import DomainError, ErrorCode
from src.domain.models import ChangeRequest

CHANGE_TRANSITIONS: dict[ChangeStatus, frozenset[ChangeStatus]] = {
    ChangeStatus.DRAFT: frozenset(
        {
            ChangeStatus.PENDING_APPROVAL,
            ChangeStatus.DEFERRED,
            ChangeStatus.NEEDS_INFO,
        }
    ),
    ChangeStatus.PENDING_APPROVAL: frozenset(
        {
            ChangeStatus.APPROVED,
            ChangeStatus.REJECTED,
            ChangeStatus.DEFERRED,
            ChangeStatus.NEEDS_INFO,
        }
    ),
    ChangeStatus.APPROVED: frozenset({ChangeStatus.PUBLISHED}),
    ChangeStatus.NEEDS_INFO: frozenset({ChangeStatus.DRAFT}),
    ChangeStatus.DEFERRED: frozenset({ChangeStatus.DRAFT}),
}


def ensure_change_transition(current: ChangeStatus, target: ChangeStatus) -> None:
    if target not in CHANGE_TRANSITIONS.get(current, frozenset()):
        raise DomainError(
            ErrorCode.INVALID_CHANGE_TRANSITION,
            f"{current.value} -> {target.value}",
        )


def transition_change(
    change: ChangeRequest,
    target: ChangeStatus,
    *,
    review_action: ChangeReviewAction | None = None,
    reviewed_by: str | None = None,
    review_comment: str | None = None,
    review_idempotency_key: str | None = None,
    reviewed_at: datetime | None = None,
    updated_at: datetime,
) -> ChangeRequest:
    ensure_change_transition(change.status, target)
    data = change.model_dump()
    if target == ChangeStatus.PUBLISHED:
        review_action = change.review_action
        reviewed_by = change.reviewed_by
        review_comment = change.review_comment
        review_idempotency_key = change.review_idempotency_key
        reviewed_at = change.reviewed_at
    data.update(
        {
            "status": target,
            "review_action": review_action,
            "reviewed_by": reviewed_by,
            "review_comment": review_comment,
            "review_idempotency_key": review_idempotency_key,
            "reviewed_at": reviewed_at,
            "updated_at": updated_at,
        }
    )
    return ChangeRequest.model_validate(data)
