from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from src.application.ports.incubator import IncubatorProjectRepository
from src.domain.enums import ProjectRootStatus
from src.domain.errors import DomainError, ErrorCode
from src.infrastructure.files.project_library import ProjectPaths

_REQUIRED_DIRECTORIES = ("raw", "wiki", "schema", "exports", ".incubator")


class ProjectPathResolver:
    """Resolves project content roots only from central project registrations."""

    def __init__(
        self,
        library_root: Path,
        projects: IncubatorProjectRepository,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.library_root = library_root.expanduser().resolve()
        self.projects = projects
        self.now = now or (lambda: datetime.now(UTC))

    def resolve(self, project_id: str) -> ProjectPaths:
        project = self.projects.get(project_id)
        if project.project_root_path is None:
            raise DomainError(ErrorCode.PROJECT_ROOT_UNAVAILABLE)

        registered_root = Path(project.project_root_path)
        if registered_root.expanduser().absolute().is_symlink():
            raise DomainError(ErrorCode.PROJECT_ROOT_UNAVAILABLE)
        try:
            paths = self.validate_relocation(project_id, registered_root)
        except DomainError:
            self.projects.update_root_location(
                project_id,
                registered_root,
                ProjectRootStatus.UNAVAILABLE,
                self.now(),
            )
            raise

        self.projects.update_root_location(
            project_id,
            paths.project_root,
            ProjectRootStatus.AVAILABLE,
            self.now(),
        )
        return paths

    def validate_parent(self, parent_root: Path, project_id: str) -> Path:
        parent = parent_root.expanduser().resolve()
        if not parent.is_dir() or not os.access(parent, os.W_OK):
            raise DomainError(ErrorCode.PROJECT_ROOT_NOT_WRITABLE)
        target = parent / project_id
        if target.exists() or target.is_symlink():
            raise DomainError(ErrorCode.PROJECT_ROOT_ALREADY_EXISTS)
        try:
            ProjectPaths.for_registered_root(self.library_root, project_id, target)
        except ValueError as error:
            raise DomainError(ErrorCode.PROJECT_ROOT_NOT_WRITABLE) from error
        return parent

    def validate_relocation(self, project_id: str, project_root: Path) -> ProjectPaths:
        try:
            paths = ProjectPaths.for_registered_root(self.library_root, project_id, project_root)
        except ValueError as error:
            raise DomainError(ErrorCode.PROJECT_ROOT_UNAVAILABLE) from error

        if not paths.project_root.is_dir():
            raise DomainError(ErrorCode.PROJECT_ROOT_UNAVAILABLE)
        has_required_directories = all(
            (paths.project_root / directory).is_dir() for directory in _REQUIRED_DIRECTORIES
        )
        if not has_required_directories:
            raise DomainError(ErrorCode.PROJECT_ROOT_UNAVAILABLE)

        try:
            payload = json.loads((paths.system_root / "project.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise DomainError(ErrorCode.PROJECT_ROOT_UNAVAILABLE) from error
        if not isinstance(payload, dict) or payload.get("project_id") != project_id:
            raise DomainError(ErrorCode.PROJECT_ROOT_ID_MISMATCH)
        return paths
