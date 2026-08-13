from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.application.dto.projects import CreateProjectInput
from src.domain.enums import BaselineStatus
from src.domain.incubator import IncubatorSettings
from src.domain.models import Baseline, BaselineManifest, Project, SourceRecord
from src.infrastructure.db.migrations import migrate
from src.infrastructure.db.repositories import SqliteBaselineRepository, SqliteKnowledgeRepository
from src.infrastructure.files.manifest_store import ManifestStore
from src.infrastructure.files.project_library import JsonIncubatorSettingsStore, ProjectPaths
from src.infrastructure.files.project_scaffolder import ProjectScaffolder


class MigrationResult:
    def __init__(self, status: str, project_root: Path | None = None) -> None:
        self.status = status
        self.project_root = project_root


def migrate_lld(source_root: Path, library_root: Path, *, dry_run: bool = False) -> MigrationResult:
    """Copy the effective 1.x LLD assets into an isolated, idempotent 2.0 project."""
    source_root = source_root.expanduser().resolve()
    library_root = library_root.expanduser().resolve()
    legacy = _read_legacy(source_root)
    target = ProjectPaths.for_project(library_root, "LLD")
    if target.project_root.exists():
        _validate_existing(target, legacy["manifest"])
        return MigrationResult("ALREADY_MIGRATED", target.project_root)
    if dry_run:
        return MigrationResult("DRY_RUN_OK")

    schema_source = Path(__file__).resolve().parents[1] / "assets/incubator_schema"
    scaffolder = ProjectScaffolder(
        library_root=library_root,
        schema_source=schema_source,
        now=lambda: legacy["manifest"].published_at,
    )
    prepared = scaffolder.prepare(
        CreateProjectInput(
            project_id="LLD",
            name=legacy["project"].name,
            description=legacy["project"].product_line,
            initial_display_version=legacy["manifest"].display_version,
            allow_external_model=legacy["project"].allow_external_model,
        )
    )
    try:
        _write_project_tree(prepared.temp_root, target, legacy)
        _validate_staged_project(prepared.temp_root, legacy["manifest"])
        scaffolder.validate(prepared)
        committed = scaffolder.commit(prepared)
    except BaseException:
        scaffolder.abort(prepared)
        raise
    try:
        _write_library_database(library_root, committed, legacy)
        _ensure_library_settings(library_root, legacy["manifest"])
    except BaseException:
        raise RuntimeError("MIGRATION_DATABASE_WRITE_FAILED") from None
    return MigrationResult("MIGRATED", committed.project_root)


def _ensure_library_settings(library_root: Path, manifest: BaselineManifest) -> None:
    settings = JsonIncubatorSettingsStore(library_root)
    if settings.load() is not None:
        return
    settings.save(
        IncubatorSettings(
            owner_name=manifest.approved_by,
            library_root=str(library_root),
            current_project_id="LLD",
        )
    )


def _read_legacy(source_root: Path) -> dict:
    manifest_path = source_root / "data/local_state/current_baseline.json"
    manifest = ManifestStore(manifest_path).read_and_validate()
    if manifest.project_id != "LLD":
        raise ValueError("LEGACY_PROJECT_NOT_LLD")
    db_path = source_root / "data/local_state/product_intelligence.db"
    project, baseline, sources = _read_legacy_database(db_path, manifest)
    if baseline.project_id != "LLD" or baseline.version != manifest.current_version:
        raise ValueError("LEGACY_BASELINE_MISMATCH")
    document = _read_hashed_file(
        source_root, manifest.full_document_path, manifest.full_document_sha256
    )
    cards = _read_hashed_file(
        source_root, manifest.card_snapshot_path, manifest.card_snapshot_sha256
    )
    for source in sources:
        archive = _resolve_legacy_archive(source_root, source)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != source.sha256:
            raise ValueError(f"LEGACY_SOURCE_HASH_MISMATCH:{source.id}")
    return {
        "manifest": manifest,
        "project": project,
        "baseline": baseline,
        "document": document,
        "cards": cards,
        "sources": sources,
        "source_root": source_root,
    }


def _read_hashed_file(root: Path, relative: str, digest: str) -> bytes:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("LEGACY_ASSET_PATH_INVALID")
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != digest:
        raise ValueError("LEGACY_ASSET_HASH_MISMATCH")
    return payload


def _read_legacy_database(
    db_path: Path, manifest: BaselineManifest
) -> tuple[Project, Baseline, list[SourceRecord]]:
    # SQLite may checkpoint a live WAL while opening a source database. Read a
    # disposable copy instead, so --dry-run never changes 1.x files.
    with tempfile.TemporaryDirectory(prefix="lld-migration-read-") as temporary:
        copied_db = Path(temporary) / db_path.name
        shutil.copyfile(db_path, copied_db)
        for suffix in ("-wal", "-shm"):
            sidecar = db_path.with_name(f"{db_path.name}{suffix}")
            if sidecar.is_file():
                shutil.copyfile(sidecar, copied_db.with_name(f"{copied_db.name}{suffix}"))
        return _read_legacy_database_copy(copied_db, manifest)


def _read_legacy_database_copy(
    db_path: Path, manifest: BaselineManifest
) -> tuple[Project, Baseline, list[SourceRecord]]:
    uri = f"file:{db_path.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        project_row = connection.execute("SELECT * FROM projects WHERE id = 'LLD'").fetchone()
        baseline_row = connection.execute(
            "SELECT * FROM baselines WHERE id = ?", (manifest.current_baseline_id,)
        ).fetchone()
        source_rows = connection.execute(
            "SELECT * FROM source_records WHERE project_id = 'LLD' ORDER BY created_at, id"
        ).fetchall()
    if project_row is None or baseline_row is None:
        raise ValueError("LEGACY_DATABASE_RECORD_MISSING")
    project_data = dict(project_row)
    project_data["allow_external_model"] = bool(project_data["allow_external_model"])
    sources: list[SourceRecord] = []
    for row in source_rows:
        source_data = dict(row)
        for field in ("is_redacted", "allow_external_model", "is_sandbox"):
            source_data[field] = bool(source_data[field])
        sources.append(SourceRecord.model_validate(source_data))
    return (
        Project.model_validate(project_data),
        Baseline.model_validate(dict(baseline_row)),
        sources,
    )


def _resolve_legacy_archive(source_root: Path, source: SourceRecord) -> Path:
    candidate = Path(source.archive_path)
    archive = candidate if candidate.is_absolute() else source_root / candidate
    archive = archive.resolve()
    archive_root = (source_root / "data/source_archive/LLD").resolve()
    if not archive.is_relative_to(archive_root) or not archive.is_file():
        raise ValueError(f"LEGACY_ARCHIVE_PATH_INVALID:{source.id}")
    return archive


def _write_project_tree(temp_root: Path, target: ProjectPaths, legacy: dict) -> None:
    manifest: BaselineManifest = legacy["manifest"]
    version_dir = temp_root / "wiki/versions" / manifest.current_version
    version_dir.mkdir(parents=True, exist_ok=False)
    document_path = version_dir / "产品方案.md"
    cards_path = version_dir / "cards.json"
    document_path.write_bytes(legacy["document"])
    cards_path.write_bytes(legacy["cards"])
    current_path = temp_root / "wiki/current/当前产品方案.md"
    current_path.write_bytes(legacy["document"])
    new_manifest = BaselineManifest(
        schema_version="2.0",
        project_id="LLD",
        current_baseline_id=manifest.current_baseline_id,
        current_version=manifest.current_version,
        parent_baseline_id=manifest.parent_baseline_id,
        full_document_path=(
            Path("wiki/versions") / manifest.current_version / "产品方案.md"
        ).as_posix(),
        card_snapshot_path=(
            Path("wiki/versions") / manifest.current_version / "cards.json"
        ).as_posix(),
        full_document_sha256=hashlib.sha256(legacy["document"]).hexdigest(),
        card_snapshot_sha256=hashlib.sha256(legacy["cards"]).hexdigest(),
        change_request_id=None,
        approved_by=manifest.approved_by,
        published_at=manifest.published_at,
        display_version=manifest.display_version or manifest.current_version,
    )
    ManifestStore(
        temp_root / ".incubator/current-baseline.json", project_root=temp_root
    ).atomic_replace(new_manifest)
    index_sources = []
    for source in legacy["sources"]:
        archive = _resolve_legacy_archive(legacy["source_root"], source)
        destination = (
            temp_root
            / "raw"
            / str(source.document_date.year)
            / source.id
            / source.original_filename
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(archive, destination)
        if hashlib.sha256(destination.read_bytes()).hexdigest() != source.sha256:
            raise ValueError(f"MIGRATED_SOURCE_HASH_MISMATCH:{source.id}")
        index_sources.append(
            {
                "source_id": source.id,
                "filename": source.original_filename,
                "archive_path": str(
                    target.raw_root
                    / str(source.document_date.year)
                    / source.id
                    / source.original_filename
                ),
                "sha256": source.sha256,
                "source_type": source.source_type,
                "ingest_status": source.ingest_status,
                "created_at": source.created_at.isoformat(),
            }
        )
    (temp_root / ".incubator/source-index.json").write_text(
        json.dumps(
            {"schema_version": "2.0", "project_id": "LLD", "sources": index_sources},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _validate_staged_project(temp_root: Path, expected: BaselineManifest) -> None:
    manifest = ManifestStore(
        temp_root / ".incubator/current-baseline.json", project_root=temp_root
    ).read_and_validate()
    if manifest.current_version != expected.current_version:
        raise ValueError("MIGRATION_VERSION_MISMATCH")
    current = temp_root / "wiki/current/当前产品方案.md"
    version = temp_root / manifest.full_document_path
    if current.read_bytes() != version.read_bytes():
        raise ValueError("MIGRATION_CURRENT_MISMATCH")
    if hashlib.sha256(current.read_bytes()).hexdigest() != manifest.full_document_sha256:
        raise ValueError("MIGRATION_DOCUMENT_HASH_MISMATCH")


def _write_library_database(library_root: Path, target: ProjectPaths, legacy: dict) -> None:
    db_path = library_root / ".incubator/product_incubator.db"
    migrate(db_path)
    from src.infrastructure.db.repositories import (
        SqliteProjectRepository,
        SqliteSourceRepository,
    )

    projects = SqliteProjectRepository(db_path)
    try:
        projects.get("LLD")
    except KeyError:
        pass
    else:
        raise ValueError("MIGRATION_PROJECT_ALREADY_EXISTS")
    legacy_project: Project = legacy["project"]
    manifest = ManifestStore(
        target.manifest_path, project_root=target.project_root
    ).read_and_validate()
    projects.add(
        legacy_project.model_copy(update={"current_baseline_id": manifest.current_baseline_id})
    )
    sources = SqliteSourceRepository(db_path)
    for source in legacy["sources"]:
        archive_path = (
            target.raw_root / str(source.document_date.year) / source.id / source.original_filename
        )
        sources.add(source.model_copy(update={"archive_path": str(archive_path)}))
    baseline: Baseline = legacy["baseline"]
    SqliteBaselineRepository(db_path).add(
        baseline.model_copy(
            update={
                "full_document_path": manifest.full_document_path,
                "card_snapshot_path": manifest.card_snapshot_path,
                "manifest_sha256": hashlib.sha256(target.manifest_path.read_bytes()).hexdigest(),
                "full_document_sha256": manifest.full_document_sha256,
                "card_snapshot_sha256": manifest.card_snapshot_sha256,
                "display_version": manifest.display_version,
                "status": BaselineStatus.EFFECTIVE,
            }
        )
    )
    from src.domain.models import KnowledgeCard

    cards = [
        KnowledgeCard.model_validate(card).model_copy(
            update={"product_version": manifest.current_version}
        )
        for card in json.loads(legacy["cards"].decode("utf-8"))
    ]
    SqliteKnowledgeRepository(db_path).upsert_cards(cards)


def _validate_existing(target: ProjectPaths, legacy_manifest: BaselineManifest) -> None:
    if not target.manifest_path.is_file():
        raise ValueError("MIGRATION_TARGET_CONFLICT")
    manifest = ManifestStore(
        target.manifest_path, project_root=target.project_root
    ).read_and_validate()
    if (
        manifest.project_id != "LLD"
        or manifest.current_version != legacy_manifest.current_version
        or manifest.full_document_sha256 != legacy_manifest.full_document_sha256
        or manifest.card_snapshot_sha256 != legacy_manifest.card_snapshot_sha256
    ):
        raise ValueError("MIGRATION_TARGET_CONFLICT")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migrate the 1.x LLD project into 2.0.")
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--library-root", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    arguments = parser.parse_args(argv)
    result = migrate_lld(arguments.source_root, arguments.library_root, dry_run=arguments.dry_run)
    print(f"{result.status} project=LLD")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
