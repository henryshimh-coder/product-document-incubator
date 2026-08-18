from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class ProjectDto(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class CreateProjectInput(ProjectDto):
    project_id: str = Field(pattern=r"^[A-Z0-9][A-Z0-9_-]{0,63}$")
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=500)
    initial_display_version: str | None = Field(default=None, max_length=50)
    allow_external_model: bool = False
    parent_root: Path | None = None


class RelocateProjectInput(ProjectDto):
    project_id: str = Field(pattern=r"^[A-Z0-9][A-Z0-9_-]{0,63}$")
    project_root: Path


class ProjectSelection(ProjectDto):
    project_id: str = Field(pattern=r"^[A-Z0-9][A-Z0-9_-]{0,63}$")
    project_root: Path
