from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from uuid import uuid4

from src.application.dto.release import ReviewChangeRequestInput
from src.application.ports.repositories import ChangeRepository, ReviewUnitOfWork
from src.domain.enums import ChangeReviewAction, ChangeStatus
from src.domain.errors import DomainError, ErrorCode
from src.domain.models import ChangeRequest, EventLog

REVIEW_TARGET_STATUS = {
    ChangeReviewAction.APPROVE: ChangeStatus.APPROVED,
    ChangeReviewAction.REJECT: ChangeStatus.REJECTED,
    ChangeReviewAction.DEFER: ChangeStatus.DEFERRED,
    ChangeReviewAction.REQUEST_INFO: ChangeStatus.NEEDS_INFO,
}


class ReviewChangeRequest:
    """Record a human review decision for a pending-approval change request."""

    def __init__(
        self,
        *,
        changes: ChangeRepository,
        unit_of_work: ReviewUnitOfWork,
        now: Callable[[], datetime],
        event_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.changes = changes
        self.unit_of_work = unit_of_work
        self.now = now
        self.event_id_factory = event_id_factory or (lambda: f"EVENT-{uuid4().hex.upper()}")

    def execute(self, command: ReviewChangeRequestInput) -> ChangeRequest:
        comment = command.comment.strip()
        if not 10 <= len(comment) <= 200:
            raise DomainError(ErrorCode.CHANGE_REVIEW_INVALID, "INVALID_REVIEW_COMMENT")
        existing = self.changes.find_by_review_idempotency_key(command.idempotency_key)
        if existing is not None:
            if not _same_command(existing, command, comment):
                raise DomainError(ErrorCode.REVIEW_IDEMPOTENCY_CONFLICT)
            return existing
        try:
            change = self.changes.get(command.change_request_id)
        except KeyError as error:
            raise DomainError(ErrorCode.CHANGE_NOT_REVIEWABLE, "CHANGE_NOT_FOUND") from error
        if change.status != ChangeStatus.PENDING_APPROVAL:
            raise DomainError(ErrorCode.CHANGE_NOT_REVIEWABLE)
        reviewed_at = self.now()
        target_status = REVIEW_TARGET_STATUS[command.action]
        event = EventLog(
            id=self.event_id_factory(),
            project_id=change.project_id,
            event_type="change_reviewed",
            entity_type="change_request",
            entity_id=change.id,
            actor=command.reviewed_by,
            correlation_id=f"REVIEW-{change.id}",
            payload={
                "change_request_id": change.id,
                "action": command.action.value,
                "target_status": target_status.value,
                "target_version": change.target_version,
                "reviewed_by": command.reviewed_by,
                "comment": comment,
                "idempotency_key": command.idempotency_key,
            },
            created_at=reviewed_at,
        )
        return self.unit_of_work.record_review(
            change_id=change.id,
            action=command.action,
            reviewed_by=command.reviewed_by,
            comment=comment,
            idempotency_key=command.idempotency_key,
            reviewed_at=reviewed_at,
            expected_status=ChangeStatus.PENDING_APPROVAL,
            target_status=target_status,
            event=event,
        )


def _same_command(
    existing: ChangeRequest,
    command: ReviewChangeRequestInput,
    comment: str,
) -> bool:
    return (
        existing.id == command.change_request_id
        and existing.review_action == command.action
        and existing.reviewed_by == command.reviewed_by
        and existing.review_comment == comment
    )
