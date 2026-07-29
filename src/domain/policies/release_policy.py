from __future__ import annotations

from typing import Protocol

from src.domain.enums import ChangeReviewAction, ChangeStatus
from src.domain.errors import DomainError, ErrorCode
from src.domain.models import BaselineManifest, ChangeRequest


class ReleaseCommand(Protocol):
    project_id: str
    change_request_id: str
    approved_by: str
    impact_reviewed: bool
    release_note: str


class ReleasePolicy:
    def validate(
        self,
        command: ReleaseCommand,
        manifest: BaselineManifest,
        change: ChangeRequest,
        *,
        target_version_exists: bool,
        manifest_integrity_ok: bool,
    ) -> None:
        if change.status != ChangeStatus.APPROVED:
            raise DomainError(ErrorCode.CHANGE_NOT_APPROVED)
        review_fields = (
            change.reviewed_by,
            change.review_comment,
            change.review_idempotency_key,
            change.reviewed_at,
        )
        if change.review_action != ChangeReviewAction.APPROVE or any(
            item is None for item in review_fields
        ):
            raise DomainError(ErrorCode.CHANGE_REVIEW_INVALID)
        if command.change_request_id != change.id:
            raise DomainError(ErrorCode.RELEASE_CHANGE_MISMATCH)
        if len({command.project_id, manifest.project_id, change.project_id}) != 1:
            raise DomainError(ErrorCode.RELEASE_PROJECT_MISMATCH)
        if not manifest_integrity_ok:
            raise DomainError(ErrorCode.BASELINE_INTEGRITY_FAILED)
        if not command.impact_reviewed:
            raise DomainError(ErrorCode.IMPACT_REVIEW_REQUIRED)
        release_note = command.release_note.strip()
        if not 20 <= len(release_note) <= 200:
            raise DomainError(ErrorCode.INVALID_RELEASE_NOTE)
        if not command.approved_by.strip():
            raise DomainError(ErrorCode.RELEASE_APPROVER_REQUIRED)
        if change.target_version == manifest.current_version:
            raise DomainError(ErrorCode.TARGET_VERSION_ALREADY_EFFECTIVE)
        if target_version_exists:
            raise DomainError(ErrorCode.TARGET_VERSION_ALREADY_EXISTS)
