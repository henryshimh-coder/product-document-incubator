from __future__ import annotations

from typing import Protocol

from src.domain.enums import ChangeStatus
from src.domain.errors import DomainError
from src.domain.models import BaselineManifest, ChangeRequest


class ReleaseCommand(Protocol):
    project_id: str
    approved_by: str
    impact_reviewed: bool
    release_note: str


class ReleasePolicy:
    def validate(
        self,
        command: ReleaseCommand,
        manifest: BaselineManifest,
        change: ChangeRequest,
    ) -> None:
        if change.status != ChangeStatus.APPROVED:
            raise DomainError("CHANGE_NOT_APPROVED")
        if len({command.project_id, manifest.project_id, change.project_id}) != 1:
            raise DomainError("RELEASE_PROJECT_MISMATCH")
        if not command.impact_reviewed:
            raise DomainError("IMPACT_REVIEW_REQUIRED")
        release_note = command.release_note.strip()
        if not 20 <= len(release_note) <= 200:
            raise DomainError("INVALID_RELEASE_NOTE")
        if not command.approved_by.strip():
            raise DomainError("RELEASE_APPROVER_REQUIRED")
        if change.target_version == manifest.current_version:
            raise DomainError("TARGET_VERSION_ALREADY_EFFECTIVE")
