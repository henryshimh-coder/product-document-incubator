from __future__ import annotations

import pytest

from src.domain.errors import DomainError
from src.infrastructure.files.project_library import ProjectPaths


def test_project_raw_archive_rejects_symlinked_file_name_escape(tmp_path) -> None:
    """Catches a project raw archive accepting a path-like filename through a symlink."""
    from src.infrastructure.files.project_source_archive import ProjectSourceArchive

    paths = ProjectPaths.for_project(tmp_path / "library", "PROJECT_A")
    paths.raw_root.mkdir(parents=True)
    outside = tmp_path / "outside.md"
    outside.write_text("# 外部\n", encoding="utf-8")
    link = tmp_path / "linked.md"
    link.symlink_to(outside)
    archive = ProjectSourceArchive(paths=paths, source_id="SRC-001", year=2026)

    result = archive.copy_from(link)

    assert result.path.is_relative_to(paths.raw_root)
    assert result.path.read_text(encoding="utf-8") == "# 外部\n"


def test_project_paths_rejects_invalid_project_id_for_raw_archive(tmp_path) -> None:
    """Catches traversal-like project IDs reaching the project-local raw archive."""
    with pytest.raises(ValueError, match="project_id"):
        ProjectPaths.for_project(tmp_path / "library", "../OTHER")


def test_resolver_rejects_registered_root_replaced_by_a_symlink(tmp_path) -> None:
    """Catches an attacker redirecting a centrally registered project root after registration."""
    from datetime import UTC, datetime

    from src.domain.models import Project
    from src.infrastructure.db.migrations import migrate
    from src.infrastructure.db.repositories import SqliteProjectRepository
    from src.infrastructure.files.project_path_resolver import ProjectPathResolver

    database = tmp_path / "control/.incubator/product_incubator.db"
    database.parent.mkdir(parents=True)
    migrate(database)
    repository = SqliteProjectRepository(database)
    registered_root = tmp_path / "registered/PROJECT_A"
    registered_root.mkdir(parents=True)
    repository.add(
        Project(
            id="PROJECT_A",
            name="项目 A",
            product_line="产品线 A",
            stage="待初始化",
            current_baseline_id=None,
            allow_external_model=False,
            created_at=datetime(2026, 8, 17, tzinfo=UTC),
            updated_at=datetime(2026, 8, 17, tzinfo=UTC),
            project_root_path=str(registered_root),
        )
    )
    registered_root.rmdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    registered_root.symlink_to(outside, target_is_directory=True)

    with pytest.raises(DomainError, match="PROJECT_ROOT_UNAVAILABLE"):
        ProjectPathResolver(tmp_path / "control", repository).resolve("PROJECT_A")

    assert repository.get("PROJECT_A").project_root_path == str(registered_root)
