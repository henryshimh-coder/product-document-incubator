from __future__ import annotations

from hashlib import sha256

from src.infrastructure.files.project_library import ProjectPaths


def test_archived_copy_survives_original_move(tmp_path) -> None:
    """Catches raw storage retaining a reference to an external file instead of copying it."""
    from src.infrastructure.files.project_source_archive import ProjectSourceArchive

    source = tmp_path / "outside/需求.md"
    source.parent.mkdir()
    source.write_text("# 产品需求\n\n内容", encoding="utf-8")
    paths = ProjectPaths.for_project(tmp_path / "library", "PROJECT_A")
    paths.raw_root.mkdir(parents=True)
    archive = ProjectSourceArchive(paths=paths, source_id="SRC-001", year=2026)

    result = archive.copy_from(source)
    source.rename(source.with_suffix(".moved"))

    assert result.path.read_text(encoding="utf-8").startswith("# 产品需求")
    assert result.sha256 == sha256(result.path.read_bytes()).hexdigest()


def test_archived_browser_bytes_are_saved_without_a_local_source_path(tmp_path) -> None:
    """Catches browser uploads requiring the Owner's absolute local filesystem path."""
    from src.infrastructure.files.project_source_archive import ProjectSourceArchive

    paths = ProjectPaths.for_project(tmp_path / "library", "PROJECT_A")
    paths.raw_root.mkdir(parents=True)
    archive = ProjectSourceArchive(paths=paths, source_id="SRC-002", year=2026)

    result = archive.save("需求说明.md", b"# requirements\n")

    assert result.path.is_relative_to(paths.raw_root)
    assert result.path.read_bytes() == b"# requirements\n"
