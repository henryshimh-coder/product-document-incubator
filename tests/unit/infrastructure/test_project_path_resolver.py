from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.domain.enums import ProjectRootStatus
from src.domain.errors import DomainError
from src.domain.models import Project
from src.infrastructure.db.migrations import migrate
from src.infrastructure.db.repositories import SqliteProjectRepository


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 8, 17, tzinfo=UTC)


@pytest.fixture
def repository(tmp_path: Path) -> SqliteProjectRepository:
    database = tmp_path / "control/.incubator/product_incubator.db"
    database.parent.mkdir(parents=True)
    migrate(database)
    return SqliteProjectRepository(database)


def seed_project(repository: SqliteProjectRepository, project_id: str, project_root: Path) -> None:
    repository.add(
        Project(
            id=project_id,
            name="项目 A",
            product_line="产品线 A",
            stage="待初始化",
            current_baseline_id=None,
            allow_external_model=False,
            created_at=datetime(2026, 8, 17, tzinfo=UTC),
            updated_at=datetime(2026, 8, 17, tzinfo=UTC),
            project_root_path=str(project_root),
        )
    )


def create_project_tree(project_root: Path, *, project_id: str) -> Path:
    for directory in ("raw", "wiki", "schema", "exports", ".incubator"):
        (project_root / directory).mkdir(parents=True, exist_ok=True)
    (project_root / ".incubator/project.json").write_text(
        json.dumps({"project_id": project_id}), encoding="utf-8"
    )
    return project_root


def test_resolver_marks_missing_root_unavailable(
    repository: SqliteProjectRepository, tmp_path: Path, now: datetime
) -> None:
    """Catches a missing registered root being silently recreated or treated as available."""
    from src.infrastructure.files.project_path_resolver import ProjectPathResolver

    seed_project(repository, "PROJECT_A", tmp_path / "missing/PROJECT_A")
    resolver = ProjectPathResolver(tmp_path / "control", repository, now=lambda: now)

    with pytest.raises(DomainError, match="PROJECT_ROOT_UNAVAILABLE"):
        resolver.resolve("PROJECT_A")

    assert repository.get("PROJECT_A").root_status is ProjectRootStatus.UNAVAILABLE
    assert not (tmp_path / "missing/PROJECT_A").exists()


def test_resolve_uses_registered_root_outside_control_and_marks_it_available(
    repository: SqliteProjectRepository, tmp_path: Path, now: datetime
) -> None:
    """Catches deriving a project root from its ID instead of the central registration."""
    from src.infrastructure.files.project_path_resolver import ProjectPathResolver

    project_root = create_project_tree(tmp_path / "external/PROJECT_A", project_id="PROJECT_A")
    seed_project(repository, "PROJECT_A", project_root)
    resolver = ProjectPathResolver(tmp_path / "control", repository, now=lambda: now)

    paths = resolver.resolve("PROJECT_A")

    assert paths.project_root == project_root.resolve()
    assert paths.library_root == (tmp_path / "control").resolve()
    saved = repository.get("PROJECT_A")
    assert saved.root_status is ProjectRootStatus.AVAILABLE
    assert saved.root_last_verified_at == now


def test_validate_relocation_requires_matching_project_json(
    repository: SqliteProjectRepository, tmp_path: Path
) -> None:
    """Catches relocating a project onto another project's content tree."""
    from src.infrastructure.files.project_path_resolver import ProjectPathResolver

    wrong = create_project_tree(tmp_path / "PROJECT_B", project_id="PROJECT_B")
    resolver = ProjectPathResolver(tmp_path / "control", repository)

    with pytest.raises(DomainError, match="PROJECT_ROOT_ID_MISMATCH"):
        resolver.validate_relocation("PROJECT_A", wrong)


def test_validate_parent_rejects_an_existing_target(
    repository: SqliteProjectRepository, tmp_path: Path
) -> None:
    """Catches project creation replacing an existing directory in the selected parent."""
    from src.infrastructure.files.project_path_resolver import ProjectPathResolver

    parent = tmp_path / "external"
    (parent / "PROJECT_A").mkdir(parents=True)
    resolver = ProjectPathResolver(tmp_path / "control", repository)

    with pytest.raises(DomainError, match="PROJECT_ROOT_ALREADY_EXISTS"):
        resolver.validate_parent(parent, "PROJECT_A")


def test_validate_parent_rejects_a_broken_symlink_target(
    repository: SqliteProjectRepository, tmp_path: Path
) -> None:
    """Catches treating a dangling target symlink as an absent directory to replace."""
    from src.infrastructure.files.project_path_resolver import ProjectPathResolver

    parent = tmp_path / "external"
    parent.mkdir()
    (parent / "PROJECT_A").symlink_to(tmp_path / "missing-target", target_is_directory=True)
    resolver = ProjectPathResolver(tmp_path / "control", repository)

    with pytest.raises(DomainError, match="PROJECT_ROOT_ALREADY_EXISTS"):
        resolver.validate_parent(parent, "PROJECT_A")
