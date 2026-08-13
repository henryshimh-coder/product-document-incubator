from __future__ import annotations

import pytest

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
