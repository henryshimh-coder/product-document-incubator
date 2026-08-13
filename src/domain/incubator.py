from __future__ import annotations

from datetime import datetime

from pydantic import Field

from src.domain.enums import DocumentDraftStatus
from src.domain.models import DomainModel, NonEmptyStr, Sha256Str


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
