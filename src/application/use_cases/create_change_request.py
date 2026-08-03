from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from src.application.dto.decision import CreateChangeRequestInput
from src.domain.enums import ChangeStatus
from src.domain.errors import DomainError, ErrorCode
from src.domain.models import ChangeRequest, Decision, IssueCard


class CreateChangeRequest:
    """Build a validated pending-approval change without persisting it independently."""

    def __init__(
        self,
        *,
        id_factory: Callable[[], str],
        now: Callable[[], datetime],
    ) -> None:
        self.id_factory = id_factory
        self.now = now

    def build(
        self,
        command: CreateChangeRequestInput,
        *,
        issue: IssueCard,
        decision: Decision,
    ) -> ChangeRequest:
        required_text = (
            command.target_card_id,
            command.before_content,
            command.after_content,
            command.rationale,
            command.responsible_domain,
            command.required_approver_role,
            command.demo_confirmer,
            command.target_version,
            command.effective_condition,
        )
        if (
            any(value is None or not value.strip() for value in required_text)
            or command.before_content is None
            or command.after_content is None
            or command.before_content.strip() == command.after_content.strip()
            or not command.evidence_refs
            or not all(value.strip() for value in command.evidence_refs)
            or not command.impacted_objects
            or not all(value.strip() for value in command.impacted_objects)
        ):
            raise DomainError(ErrorCode.CHANGE_FIELDS_REQUIRED)
        created_at = self.now()
        return ChangeRequest(
            id=self.id_factory(),
            project_id=issue.project_id,
            issue_id=issue.id,
            decision_id=decision.id,
            target_card_id=command.target_card_id,
            before_content=command.before_content,
            after_content=command.after_content,
            rationale=command.rationale,
            evidence_refs=command.evidence_refs,
            impacted_objects=command.impacted_objects,
            responsible_domain=command.responsible_domain,
            required_approver_role=command.required_approver_role,
            demo_confirmer=command.demo_confirmer,
            status=ChangeStatus.PENDING_APPROVAL,
            review_action=None,
            reviewed_by=None,
            review_comment=None,
            review_idempotency_key=None,
            reviewed_at=None,
            target_version=command.target_version,
            effective_condition=command.effective_condition,
            created_at=created_at,
            updated_at=created_at,
        )
