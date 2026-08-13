from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.infrastructure.db.repositories import SqliteProjectRepository, SqliteSourceRepository
from src.infrastructure.files.manifest_store import ManifestStore
from src.infrastructure.files.project_library import JsonIncubatorSettingsStore, ProjectPaths


@dataclass(frozen=True)
class ValidationReport:
    projects: int
    current_projects: int
    sources: int


def validate_incubator(library_root: Path) -> ValidationReport:
    library_root = library_root.expanduser().resolve()
    db_path = library_root / ".incubator/product_incubator.db"
    if not db_path.is_file():
        raise ValueError("INCUBATOR_DATABASE_MISSING")
    settings = JsonIncubatorSettingsStore(library_root).load()
    if settings is None:
        raise ValueError("INCUBATOR_SETTINGS_MISSING")
    projects = SqliteProjectRepository(db_path).list_all()
    sources = SqliteSourceRepository(db_path)
    current_projects = 0
    source_count = 0
    for project in projects:
        paths = ProjectPaths.for_project(library_root, project.id)
        _validate_project(paths, sources.list_for_project(project.id))
        source_count += len(sources.list_for_project(project.id))
        if paths.manifest_path.is_file():
            current_projects += 1
    return ValidationReport(
        projects=len(projects), current_projects=current_projects, sources=source_count
    )


def _validate_project(paths: ProjectPaths, sources) -> None:
    for directory in (paths.raw_root, paths.wiki_root, paths.schema_root, paths.exports_root):
        if not directory.is_dir():
            raise ValueError(f"INCUBATOR_DIRECTORY_MISSING:{directory.name}")
    for source in sources:
        archive = Path(source.archive_path).resolve()
        if not archive.is_relative_to(paths.raw_root.resolve()) or not archive.is_file():
            raise ValueError(f"INCUBATOR_SOURCE_PATH_INVALID:{source.id}")
        if hashlib.sha256(archive.read_bytes()).hexdigest() != source.sha256:
            raise ValueError(f"INCUBATOR_SOURCE_HASH_MISMATCH:{source.id}")
    index_path = paths.system_root / "source-index.json"
    if not index_path.is_file():
        raise ValueError("INCUBATOR_SOURCE_INDEX_MISSING")
    index = json.loads(index_path.read_text(encoding="utf-8"))
    if index.get("project_id") != paths.project_id:
        raise ValueError("INCUBATOR_SOURCE_INDEX_PROJECT_MISMATCH")
    if not paths.manifest_path.is_file():
        return
    manifest = ManifestStore(
        paths.manifest_path, project_root=paths.project_root
    ).read_and_validate()
    if manifest.project_id != paths.project_id:
        raise ValueError("INCUBATOR_MANIFEST_PROJECT_MISMATCH")
    version = (paths.project_root / manifest.full_document_path).resolve()
    current = paths.wiki_root / "current" / "当前产品方案.md"
    if (
        not version.is_relative_to((paths.wiki_root / "versions").resolve())
        or not version.is_file()
    ):
        raise ValueError("INCUBATOR_VERSION_PATH_INVALID")
    if not current.is_file() or current.read_bytes() != version.read_bytes():
        raise ValueError("INCUBATOR_CURRENT_MIRROR_MISMATCH")
    if hashlib.sha256(current.read_bytes()).hexdigest() != manifest.full_document_sha256:
        raise ValueError("INCUBATOR_DOCUMENT_HASH_MISMATCH")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a product document incubator library.")
    parser.add_argument("--library-root", type=Path, required=True)
    arguments = parser.parse_args(argv)
    report = validate_incubator(arguments.library_root)
    print(
        "INCUBATOR_VALIDATION_OK "
        f"projects={report.projects} current_projects={report.current_projects} "
        f"sources={report.sources}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
