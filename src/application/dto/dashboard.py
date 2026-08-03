from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.domain.models import Baseline, Project


class GetDashboardInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    project_id: str = Field(min_length=1)


class DashboardView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    project: Project
    current_baseline: Baseline | None
    open_issue_count: int = Field(ge=0)
    candidate_change_count: int = Field(ge=0)
    source_count: int = Field(ge=0)
    recent_events: list[dict[str, Any]]
    integrity_ok: bool
