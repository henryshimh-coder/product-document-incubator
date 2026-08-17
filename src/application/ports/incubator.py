from __future__ import annotations

from collections.abc import MutableMapping
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from src.application.dto.documents import (
    ArchivedSourceView,
    ArchiveRawSourceInput,
    ExportCurrentDocumentInput,
    ExportedDocument,
    IncubateDocumentInput,
    IncubationView,
    PublishDocumentDraftInput,
    SuggestStructureInput,
)
from src.application.dto.projects import CreateProjectInput, ProjectSelection
from src.domain.enums import ProjectRootStatus
from src.domain.incubator import (
    DocumentDraft,
    IncubatorSettings,
    ProjectSummary,
    StructureSuggestion,
)
from src.domain.models import Baseline, Project


class IncubatorSettingsStore(Protocol):
    @property
    def settings_path(self) -> Path: ...

    def load(self) -> IncubatorSettings | None: ...

    def save(self, settings: IncubatorSettings) -> None: ...


class IncubatorProjectRepository(Protocol):
    def add(self, project: Project) -> None: ...

    def get(self, project_id: str) -> Project: ...

    def list_all(self) -> list[Project]: ...

    def update_root_location(
        self,
        project_id: str,
        project_root: Path,
        status: ProjectRootStatus,
        verified_at: datetime | None,
    ) -> None: ...


class ProjectManagement(Protocol):
    settings: IncubatorSettingsStore

    def initialize(self, owner_name: str, library_root: Path) -> IncubatorSettings: ...

    def create(self, command: CreateProjectInput) -> Project: ...

    def list(self) -> list[ProjectSummary]: ...

    def switch(self, project_id: str) -> ProjectSelection: ...


class RawSourceArchiving(Protocol):
    def execute(self, command: ArchiveRawSourceInput) -> ArchivedSourceView: ...


class DocumentDraftRepository(Protocol):
    def add(self, draft: DocumentDraft) -> None: ...

    def update(self, draft: DocumentDraft) -> None: ...

    def get(self, draft_id: str) -> DocumentDraft: ...

    def list_for_project(self, project_id: str) -> list[DocumentDraft]: ...


class DocumentIncubation(Protocol):
    def execute(self, command: IncubateDocumentInput) -> IncubationView: ...

    def list_sources(self, project_id: str) -> list[dict[str, str]]: ...

    def list_drafts(self, project_id: str) -> list[DocumentDraft]: ...

    def save_draft(self, project_id: str, draft_id: str, markdown: str) -> DocumentDraft: ...


class DocumentDraftPublisher(Protocol):
    def execute(self, command: PublishDocumentDraftInput) -> Baseline: ...


class CurrentDocumentExporter(Protocol):
    def execute(self, command: ExportCurrentDocumentInput) -> ExportedDocument: ...


class DocumentStructureSuggester(Protocol):
    def execute(self, command: SuggestStructureInput) -> list[StructureSuggestion]: ...

    def list(self, project_id: str) -> list[StructureSuggestion]: ...

    def accept(self, *, project_id: str, suggestion_id: str) -> StructureSuggestion: ...

    def ignore(self, *, project_id: str, suggestion_id: str) -> StructureSuggestion: ...


class SessionStateCleaner(Protocol):
    def __call__(self, session_state: MutableMapping[str, Any]) -> None: ...
