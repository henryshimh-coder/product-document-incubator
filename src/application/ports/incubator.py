from __future__ import annotations

from collections.abc import MutableMapping
from pathlib import Path
from typing import Any, Protocol

from src.application.dto.documents import ArchivedSourceView, ArchiveRawSourceInput
from src.application.dto.projects import CreateProjectInput, ProjectSelection
from src.domain.incubator import IncubatorSettings, ProjectSummary
from src.domain.models import Project


class IncubatorSettingsStore(Protocol):
    @property
    def settings_path(self) -> Path: ...

    def load(self) -> IncubatorSettings | None: ...

    def save(self, settings: IncubatorSettings) -> None: ...


class IncubatorProjectRepository(Protocol):
    def add(self, project: Project) -> None: ...

    def get(self, project_id: str) -> Project: ...

    def list_all(self) -> list[Project]: ...


class ProjectManagement(Protocol):
    settings: IncubatorSettingsStore

    def initialize(self, owner_name: str, library_root: Path) -> IncubatorSettings: ...

    def create(self, command: CreateProjectInput) -> Project: ...

    def list(self) -> list[ProjectSummary]: ...

    def switch(self, project_id: str) -> ProjectSelection: ...


class RawSourceArchiving(Protocol):
    def execute(self, command: ArchiveRawSourceInput) -> ArchivedSourceView: ...


class SessionStateCleaner(Protocol):
    def __call__(self, session_state: MutableMapping[str, Any]) -> None: ...
