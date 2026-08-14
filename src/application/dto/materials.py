from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.domain.enums import AuthorityLevel, SecurityLevel
from src.domain.material_catalog import NEW_AUTHORITY_LEVELS, require_new_material_type


class MaterialArchiveMode(StrEnum):
    NEW_MATERIAL = "new_material"
    NEW_VERSION = "new_version"


class ArchiveRawSourceInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    project_id: str = Field(min_length=1)
    uploaded_name: str | None = None
    uploaded_bytes: bytes | None = None
    local_path: Path | None = None
    material_name: str | None = None
    archive_mode: MaterialArchiveMode = MaterialArchiveMode.NEW_MATERIAL
    target_series_id: str | None = None
    source_type: str = Field(min_length=1)
    authority_level: AuthorityLevel
    source_department: str = Field(min_length=1)
    document_date: date
    material_version: str | None = None
    document_version: str | None = None
    security_level: SecurityLevel
    is_redacted_confirmed: bool
    allow_external_model: bool

    @field_validator("source_type", mode="before")
    @classmethod
    def validate_new_source_type(cls, value: object) -> object:
        if not isinstance(value, str):
            raise ValueError("MATERIAL_TYPE_INVALID")
        require_new_material_type(value)
        return value

    @field_validator("authority_level")
    @classmethod
    def validate_new_authority_level(cls, value: AuthorityLevel) -> AuthorityLevel:
        if value not in NEW_AUTHORITY_LEVELS:
            raise ValueError("MATERIAL_AUTHORITY_INVALID")
        return value

    @model_validator(mode="after")
    def validate_upload_and_version(self) -> ArchiveRawSourceInput:
        browser_upload = self.uploaded_name is not None or self.uploaded_bytes is not None
        if browser_upload and (not self.uploaded_name or self.uploaded_bytes is None):
            raise ValueError("MATERIAL_UPLOAD_REQUIRED")
        if not browser_upload and self.local_path is None:
            raise ValueError("MATERIAL_UPLOAD_REQUIRED")
        if browser_upload and self.local_path is not None:
            raise ValueError("MATERIAL_UPLOAD_AMBIGUOUS")
        version = self.material_version or self.document_version
        if not version:
            raise ValueError("MATERIAL_VERSION_REQUIRED")
        if (
            self.material_version
            and self.document_version
            and self.material_version != self.document_version
        ):
            raise ValueError("MATERIAL_VERSION_CONFLICT")
        material_name = self.material_name
        if not material_name and self.uploaded_name:
            material_name = Path(self.uploaded_name).stem
        if not material_name and self.local_path:
            material_name = self.local_path.stem
        if not material_name:
            raise ValueError("MATERIAL_NAME_REQUIRED")
        object.__setattr__(self, "material_name", material_name)
        object.__setattr__(self, "material_version", version)
        return self


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
    material_name: str | None = None
    material_series_id: str | None = None
    previous_source_id: str | None = None
