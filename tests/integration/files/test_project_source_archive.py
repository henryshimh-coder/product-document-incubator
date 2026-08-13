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
