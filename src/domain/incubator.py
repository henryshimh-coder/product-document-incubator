from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Self

from pydantic import Field, StringConstraints, field_validator, model_validator

from src.domain.enums import (
    DocumentDraftStatus,
    DocumentGenerationMode,
    DocumentIncubationJobStatus,
    ProjectRootStatus,
    StructureSuggestionStatus,
)
from src.domain.models import DomainModel, NonEmptyStr, Sha256Str

SafeErrorCode = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        pattern=r"^[A-Z][A-Z0-9_]*(?::[A-Z][A-Z0-9_]*)?$",
    ),
]


class IncubatorSettings(DomainModel):
    owner_name: NonEmptyStr
    library_root: NonEmptyStr
    current_project_id: NonEmptyStr | None = None


class ProjectSummary(DomainModel):
    project_id: NonEmptyStr
    name: NonEmptyStr
    stage: NonEmptyStr
    current_version: NonEmptyStr | None
    source_count: int = Field(ge=0)
    draft_count: int = Field(ge=0)
    updated_at: datetime
    project_root_path: NonEmptyStr | None = None
    root_status: ProjectRootStatus = ProjectRootStatus.UNAVAILABLE
    root_last_verified_at: datetime | None = None


class DocumentSectionCitation(DomainModel):
    heading: NonEmptyStr
    source_id: NonEmptyStr
    chunk_id: NonEmptyStr
    locator: NonEmptyStr
    excerpt: NonEmptyStr


class DocumentDraft(DomainModel):
    id: NonEmptyStr
    project_id: NonEmptyStr
    version_id: NonEmptyStr
    display_version: NonEmptyStr | None = None
    parent_version_id: NonEmptyStr | None = None
    status: DocumentDraftStatus
    markdown_path: NonEmptyStr
    markdown_sha256: Sha256Str
    source_ids: list[NonEmptyStr]
    section_citations: list[DocumentSectionCitation]
    summary: NonEmptyStr
    missing_sections: list[NonEmptyStr]
    evidence_gaps: list[NonEmptyStr]
    created_at: datetime
    updated_at: datetime
    generation_mode: DocumentGenerationMode = DocumentGenerationMode.EXTERNAL_AI


class DocumentIncubationJob(DomainModel):
    id: NonEmptyStr
    project_id: NonEmptyStr
    source_ids: list[NonEmptyStr] = Field(min_length=1)
    requested_by: NonEmptyStr
    status: DocumentIncubationJobStatus
    dify_task_id: NonEmptyStr | None = None
    workflow_run_id: NonEmptyStr | None = None
    draft_id: NonEmptyStr | None = None
    error_code: SafeErrorCode | None = None
    created_at: datetime
    started_at: datetime | None = None
    updated_at: datetime
    finished_at: datetime | None = None

    @field_validator("created_at", "started_at", "updated_at", "finished_at")
    @classmethod
    def require_utc_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is not None and (
            value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value)
        ):
            raise ValueError("TIMESTAMP_MUST_BE_UTC")
        return value

    @model_validator(mode="after")
    def validate_lifecycle(self) -> Self:
        if self.updated_at < self.created_at:
            raise ValueError("UPDATED_AT_BEFORE_CREATED_AT")
        if self.started_at is not None and self.started_at < self.created_at:
            raise ValueError("STARTED_AT_BEFORE_CREATED_AT")
        if self.finished_at is not None:
            earliest = self.started_at or self.created_at
            if self.finished_at < earliest:
                raise ValueError("FINISHED_AT_BEFORE_JOB_START")

        if self.status is DocumentIncubationJobStatus.PENDING:
            if any(
                value is not None
                for value in (
                    self.started_at,
                    self.dify_task_id,
                    self.workflow_run_id,
                    self.draft_id,
                    self.error_code,
                    self.finished_at,
                )
            ):
                raise ValueError("PENDING_JOB_HAS_LIFECYCLE_OUTPUT")
        elif self.status is DocumentIncubationJobStatus.RUNNING:
            if not all((self.started_at, self.dify_task_id, self.workflow_run_id)):
                raise ValueError("RUNNING_JOB_MISSING_GATEWAY_IDENTIFIERS")
            if any(
                value is not None for value in (self.draft_id, self.error_code, self.finished_at)
            ):
                raise ValueError("RUNNING_JOB_HAS_TERMINAL_OUTPUT")
        elif self.status is DocumentIncubationJobStatus.SUCCEEDED:
            if not all(
                (
                    self.started_at,
                    self.dify_task_id,
                    self.workflow_run_id,
                    self.draft_id,
                    self.finished_at,
                )
            ):
                raise ValueError("SUCCEEDED_JOB_MISSING_OUTPUT")
            if self.error_code is not None:
                raise ValueError("SUCCEEDED_JOB_HAS_ERROR")
        elif self.status is DocumentIncubationJobStatus.FAILED:
            if self.error_code is None or self.finished_at is None:
                raise ValueError("FAILED_JOB_MISSING_SAFE_ERROR")
            if self.draft_id is not None:
                raise ValueError("FAILED_JOB_HAS_DRAFT")

        return self


class StructureSuggestion(DomainModel):
    id: NonEmptyStr
    project_id: NonEmptyStr
    title: NonEmptyStr
    reason: NonEmptyStr
    reference_project_ids: list[NonEmptyStr]
    confidence: float = Field(ge=0, le=1)
    status: StructureSuggestionStatus
    created_at: datetime
    updated_at: datetime
