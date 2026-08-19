from __future__ import annotations

import json
import os
from collections.abc import Callable, MutableMapping
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from filelock import FileLock

from src.application.dto.projects import (
    CreateProjectInput,
    ProjectSelection,
    RelocateProjectInput,
)
from src.application.ports.incubator import (
    IncubatorProjectRepository,
    IncubatorSettingsStore,
    ProjectPathResolving,
)
from src.domain.enums import ProjectRootStatus
from src.domain.errors import DomainError, ErrorCode
from src.domain.incubator import IncubatorSettings, ProjectSummary
from src.domain.models import Project
from src.infrastructure.db.migrations import migrate
from src.infrastructure.db.repositories import SqliteProjectRepository
from src.infrastructure.files.project_library import (
    JsonIncubatorSettingsStore,
    ProjectLibraryLocator,
)
from src.infrastructure.files.project_path_resolver import ProjectPathResolver
from src.infrastructure.files.project_scaffolder import ProjectScaffolder


class ManageProjects:
    def __init__(
        self,
        *,
        library_root: Path,
        projects: IncubatorProjectRepository,
        scaffolder: ProjectScaffolder,
        settings: IncubatorSettingsStore,
        now: Callable[[], datetime],
        locator: ProjectLibraryLocator | None = None,
        schema_source: Path | None = None,
        path_resolver: ProjectPathResolving | None = None,
    ) -> None:
        self.library_root = library_root.expanduser().resolve()
        self.projects = projects
        self.scaffolder = scaffolder
        self.settings = settings
        self.now = now
        self.locator = locator
        self.schema_source = schema_source
        self.path_resolver = path_resolver or ProjectPathResolver(
            self.library_root, self.projects, now=self.now
        )

    def initialize(self, owner_name: str, library_root: Path) -> IncubatorSettings:
        selected_root = library_root.expanduser().resolve()
        configured = IncubatorSettings(
            owner_name=owner_name,
            library_root=str(selected_root),
            current_project_id=None,
        )
        if selected_root != self.library_root:
            if self.locator is None or self.schema_source is None:
                raise ValueError("project manager cannot change library_root")
            database_path = selected_root / ".incubator/product_incubator.db"
            next_projects = SqliteProjectRepository(database_path)
            next_scaffolder = ProjectScaffolder(
                library_root=selected_root,
                schema_source=self.schema_source,
                now=self.now,
            )
            next_settings = JsonIncubatorSettingsStore(selected_root)
        else:
            database_path = self.library_root / ".incubator/product_incubator.db"
            next_projects = self.projects
            next_scaffolder = self.scaffolder
            next_settings = self.settings
        migrate(database_path)
        next_settings.save(configured)
        if self.locator is not None:
            self.locator.save_pointer(selected_root)
        self.library_root = selected_root
        self.projects = next_projects
        self.scaffolder = next_scaffolder
        self.settings = next_settings
        self.path_resolver = ProjectPathResolver(
            self.library_root, self.projects, now=self.now
        )
        return configured

    def create(self, command: CreateProjectInput) -> Project:
        lock_root = self.library_root / ".incubator/locks"
        lock_root.mkdir(parents=True, exist_ok=True)
        lock_path = lock_root / "project-create.lock"
        with FileLock(lock_path, timeout=5):
            return self._create_locked(command)

    def _create_locked(self, command: CreateProjectInput) -> Project:
        requested_parent = command.parent_root or self.library_root
        try:
            parent_root = self.path_resolver.validate_parent(
                requested_parent, command.project_id
            )
        except DomainError as error:
            if error.code == ErrorCode.PROJECT_ROOT_ALREADY_EXISTS.value:
                raise ValueError(f"project already exists: {command.project_id}") from error
            raise
        try:
            self.projects.get(command.project_id)
        except KeyError:
            pass
        else:
            raise ValueError(f"project already exists: {command.project_id}")

        prepared = self.scaffolder.prepare(command, parent_root=parent_root)
        committed = False
        try:
            self.scaffolder.validate(prepared)
            self.scaffolder.commit(prepared)
            committed = True
            timestamp = self.now()
            project = Project(
                id=command.project_id,
                name=command.name,
                product_line=command.description,
                stage="待初始化",
                current_baseline_id=None,
                allow_external_model=command.allow_external_model,
                created_at=timestamp,
                updated_at=timestamp,
                project_root_path=str(prepared.paths.project_root),
                root_status=ProjectRootStatus.AVAILABLE,
                root_last_verified_at=timestamp,
            )
            self.projects.add(project)
            return project
        except BaseException:
            if committed:
                self._quarantine_committed_project(
                    prepared.paths.project_root, command.project_id
                )
            else:
                self.scaffolder.abort(prepared)
            raise

    def list(self) -> list[ProjectSummary]:
        return [self._summary(project) for project in self.projects.list_all()]

    def switch(self, project_id: str) -> ProjectSelection:
        self.projects.get(project_id)
        try:
            paths = self.path_resolver.resolve(project_id)
        except DomainError as error:
            raise FileNotFoundError(f"project directory not found: {project_id}") from error
        current_settings = self.settings.load()
        if current_settings is None:
            raise RuntimeError("incubator Owner settings are required")
        self.settings.save(current_settings.model_copy(update={"current_project_id": project_id}))
        return ProjectSelection(project_id=project_id, project_root=paths.project_root)

    def relocate(self, command: RelocateProjectInput) -> ProjectSelection:
        paths = self.path_resolver.validate_relocation(
            command.project_id, command.project_root
        )
        self.projects.update_root_location(
            command.project_id,
            paths.project_root,
            ProjectRootStatus.AVAILABLE,
            self.now(),
        )
        return ProjectSelection(
            project_id=command.project_id,
            project_root=paths.project_root,
        )

    def _summary(self, project: Project) -> ProjectSummary:
        try:
            paths = self.path_resolver.resolve(project.id)
        except DomainError:
            refreshed = self.projects.get(project.id)
            return ProjectSummary(
                project_id=refreshed.id,
                name=refreshed.name,
                stage=refreshed.stage,
                current_version=None,
                source_count=0,
                draft_count=0,
                updated_at=refreshed.updated_at,
                project_root_path=refreshed.project_root_path,
                root_status=refreshed.root_status,
                root_last_verified_at=refreshed.root_last_verified_at,
            )
        refreshed = self.projects.get(project.id)
        source_count = 0
        source_index = paths.system_root / "source-index.json"
        if source_index.is_file():
            payload = json.loads(source_index.read_text(encoding="utf-8"))
            sources = payload.get("sources", [])
            if isinstance(sources, list):
                source_count = len(sources)
        draft_root = paths.wiki_root / "drafts"
        if draft_root.is_dir():
            local_ingest_root = draft_root / "local-ingest"
            draft_count = sum(
                1
                for item in draft_root.iterdir()
                if item.is_dir() and item.name != "local-ingest"
            )
            if local_ingest_root.is_dir():
                draft_count += sum(1 for item in local_ingest_root.iterdir() if item.is_dir())
        else:
            draft_count = 0
        current_version = None
        if paths.manifest_path.is_file():
            manifest = json.loads(paths.manifest_path.read_text(encoding="utf-8"))
            value = manifest.get("current_version")
            current_version = value if isinstance(value, str) and value.strip() else None
        return ProjectSummary(
            project_id=refreshed.id,
            name=refreshed.name,
            stage=refreshed.stage,
            current_version=current_version,
            source_count=source_count,
            draft_count=draft_count,
            updated_at=refreshed.updated_at,
            project_root_path=refreshed.project_root_path,
            root_status=refreshed.root_status,
            root_last_verified_at=refreshed.root_last_verified_at,
        )

    def _quarantine_committed_project(self, project_root: Path, project_id: str) -> None:
        if not project_root.exists():
            return
        quarantine_path = project_root.parent / f".{project_id}.quarantine-{uuid4().hex}"
        os.replace(project_root, quarantine_path)


SESSION_STATE_WHITELIST = {
    "incubator_owner",
    "incubator_library_root",
    "active_project_id",
}


def clear_project_session_state(session_state: MutableMapping[str, Any]) -> None:
    keep = {
        key: value
        for key, value in session_state.items()
        if key in SESSION_STATE_WHITELIST or (key.startswith("_pi_") and key.endswith("_page"))
    }
    session_state.clear()
    session_state.update(keep)
