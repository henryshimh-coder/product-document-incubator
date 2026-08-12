from __future__ import annotations

from datetime import datetime

from pydantic import Field

from src.domain.models import DomainModel, NonEmptyStr


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
