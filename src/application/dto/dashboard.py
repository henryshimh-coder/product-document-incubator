from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.domain.enums import BaselineStatus
from src.domain.models import BaselineManifest, Project


class GetDashboardInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    project_id: str = Field(min_length=1)


class DashboardBaselineView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    project_id: str
    version: str
    parent_baseline_id: str | None
    status: BaselineStatus
    full_document_path: str
    card_snapshot_path: str
    change_request_id: str | None
    approved_by: str
    effective_at: datetime

    @classmethod
    def from_manifest(cls, manifest: BaselineManifest) -> DashboardBaselineView:
        return cls(
            id=manifest.current_baseline_id,
            project_id=manifest.project_id,
            version=manifest.current_version,
            parent_baseline_id=manifest.parent_baseline_id,
            status=BaselineStatus.EFFECTIVE,
            full_document_path=manifest.full_document_path,
            card_snapshot_path=manifest.card_snapshot_path,
            change_request_id=manifest.change_request_id,
            approved_by=manifest.approved_by,
            effective_at=manifest.published_at,
        )


class DashboardView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    project: Project
    current_baseline: DashboardBaselineView | None
    open_issue_count: int = Field(ge=0)
    candidate_change_count: int = Field(ge=0)
    source_count: int = Field(ge=0)
    recent_events: list[dict[str, Any]]
    integrity_ok: bool
