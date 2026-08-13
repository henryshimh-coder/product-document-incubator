from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from src.domain.enums import AuthorityLevel, SecurityLevel
from src.domain.incubator import DocumentDraft


class ArchiveRawSourceInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    project_id: str = Field(min_length=1)
    local_path: Path
    source_type: str = Field(min_length=1)
    authority_level: AuthorityLevel
    source_department: str = Field(min_length=1)
    document_date: date
    document_version: str = Field(min_length=1)
    security_level: SecurityLevel
    is_redacted_confirmed: bool
    allow_external_model: bool


class ArchivedSourceView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str
    project_id: str
    filename: str
    archive_path: Path
    sha256: str
    size_bytes: int
    source_type: str
    ingest_status: str
    duplicate: bool
    created_at: datetime


class IncubateDocumentInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    project_id: str = Field(min_length=1)
    source_ids: list[str] = Field(min_length=1, max_length=200)
    requested_by: str = Field(min_length=1, max_length=100)


class IncubationView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    draft: DocumentDraft
    markdown: str


class PublishDocumentDraftInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    project_id: str = Field(min_length=1)
    draft_id: str = Field(min_length=1)
    owner_name: str = Field(min_length=1, max_length=100)
    display_version: str = Field(min_length=1, max_length=50)


class ExportCurrentDocumentInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    project_id: str = Field(min_length=1)


class ExportedDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    filename: str
    content: bytes
    sha256: str
    export_path: Path


class SuggestStructureInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    project_id: str = Field(min_length=1)
    reference_project_ids: list[str] = Field(min_length=1, max_length=20)
    requested_by: str = Field(min_length=1, max_length=100)
