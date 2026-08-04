from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime

from src.application.dto.decision import CreateChangeRequestInput
from src.application.ports.dashboard import ManifestReader
from src.application.ports.repositories import KnowledgeRepository
from src.domain.enums import ChangeStatus, KnowledgeStatus
from src.domain.errors import DomainError, ErrorCode
from src.domain.models import ChangeRequest, Decision, IssueCard


class CreateChangeRequest:
    """Build a validated pending-approval change without persisting it independently."""

    def __init__(
        self,
        *,
        id_factory: Callable[[], str],
        now: Callable[[], datetime],
        manifest: ManifestReader,
        knowledge: KnowledgeRepository,
    ) -> None:
        self.id_factory = id_factory
        self.now = now
        self.manifest = manifest
        self.knowledge = knowledge

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
        manifest = self.manifest.read_snapshot().manifest
        if manifest.project_id != issue.project_id or decision.project_id != issue.project_id:
            raise DomainError(ErrorCode.CHANGE_FIELDS_REQUIRED, "CHANGE_PROJECT_MISMATCH")
        try:
            target = self.knowledge.get_card(command.target_card_id)
        except KeyError as error:
            raise DomainError(
                ErrorCode.CHANGE_FIELDS_REQUIRED,
                "TARGET_CARD_NOT_FOUND",
            ) from error
        if (
            target.project_id != issue.project_id
            or target.status != KnowledgeStatus.EFFECTIVE
            or target.product_version != manifest.current_version
        ):
            raise DomainError(
                ErrorCode.CHANGE_FIELDS_REQUIRED,
                "TARGET_CARD_NOT_CURRENT_EFFECTIVE",
            )
        if command.before_content != target.content:
            raise DomainError(ErrorCode.CHANGE_FIELDS_REQUIRED, "BEFORE_CONTENT_MISMATCH")
        issue_citations = {item.citation_id for item in issue.evidence}
        if not set(command.evidence_refs) <= issue_citations:
            raise DomainError(ErrorCode.CHANGE_FIELDS_REQUIRED, "EVIDENCE_NOT_IN_ISSUE")
        if target.id not in command.impacted_objects:
            raise DomainError(ErrorCode.CHANGE_FIELDS_REQUIRED, "TARGET_NOT_IMPACTED")
        for impacted_id in command.impacted_objects:
            try:
                impacted = self.knowledge.get_card(impacted_id)
            except KeyError as error:
                raise DomainError(
                    ErrorCode.CHANGE_FIELDS_REQUIRED,
                    "IMPACT_NOT_FOUND",
                ) from error
            if (
                impacted.project_id != issue.project_id
                or impacted.status != KnowledgeStatus.EFFECTIVE
                or impacted.product_version != manifest.current_version
            ):
                raise DomainError(
                    ErrorCode.CHANGE_FIELDS_REQUIRED,
                    "IMPACT_NOT_CURRENT_EFFECTIVE",
                )
        if not _is_next_version(manifest.current_version, command.target_version):
            raise DomainError(ErrorCode.CHANGE_FIELDS_REQUIRED, "TARGET_VERSION_NOT_NEXT")
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


def _is_next_version(current: str, target: str) -> bool:
    current_match = re.fullmatch(r"(.+_)(\d+)", current)
    target_match = re.fullmatch(r"(.+_)(\d+)", target)
    if current_match is None or target_match is None:
        return False
    return (
        target_match.group(1) == current_match.group(1)
        and int(target_match.group(2)) == int(current_match.group(2)) + 1
    )
