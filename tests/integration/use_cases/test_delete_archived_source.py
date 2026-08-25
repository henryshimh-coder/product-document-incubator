from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.application.dto.materials import DeleteArchivedSourceInput
from src.application.use_cases.delete_archived_source import DeleteArchivedSource
from src.domain.enums import AuthorityLevel, SecurityLevel
from src.domain.errors import DomainError
from src.domain.models import Project, SourceRecord
from src.infrastructure.db.migrations import migrate
from src.infrastructure.db.repositories import SqliteProjectRepository, SqliteSourceRepository
from src.infrastructure.files.project_library import ProjectPaths
from src.infrastructure.files.source_index_store import SourceIndexStore
from src.infrastructure.files.source_trash import SourceTrash


def _make_source(
    tmp_path: Path,
    *,
    status: str,
    source_id: str = "SRC-PROJECT-A-001",
) -> tuple[ProjectPaths, Path, SqliteSourceRepository, SourceIndexStore, SourceRecord, bytes]:
    root = tmp_path / "library"
    paths = ProjectPaths.for_project(root, "PROJECT_A")
    for directory in (
        paths.raw_root,
        paths.wiki_root,
        paths.schema_root,
        paths.exports_root,
        paths.system_root,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    raw_bytes = b"# immutable source\n\nowner evidence\n"
    archive_path = paths.raw_root / "2026" / source_id / "material.md"
    archive_path.parent.mkdir(parents=True)
    archive_path.write_bytes(raw_bytes)
    digest = hashlib.sha256(raw_bytes).hexdigest()
    db_path = root / ".incubator/product_incubator.db"
    migrate(db_path)
    now = datetime(2026, 8, 24, 12, 30, tzinfo=UTC)
    SqliteProjectRepository(db_path).add(
        Project(
            id="PROJECT_A",
            name="项目 A",
            product_line="测试",
            stage="待初始化",
            current_baseline_id=None,
            allow_external_model=False,
            created_at=now,
            updated_at=now,
        )
    )
    source = SourceRecord(
        id=source_id,
        project_id="PROJECT_A",
        original_filename="material.md",
        archive_path=str(archive_path),
        sha256=digest,
        mime_type="text/markdown",
        size_bytes=len(raw_bytes),
        source_type="product_requirement",
        authority_level=AuthorityLevel.FORMAL_EFFECTIVE,
        source_department="产品部",
        provider=None,
        document_date=now.date(),
        document_version="v1.0",
        applicable_baseline_version="未关联基线",
        security_level=SecurityLevel.L2_INTERNAL,
        is_redacted=True,
        allow_external_model=False,
        is_sandbox=False,
        ingest_status=status,
        created_at=now,
        material_name="产品原则",
        material_series_id="MAT-PROJECT-A-001",
    )
    repository = SqliteSourceRepository(db_path)
    repository.add(source)
    index = SourceIndexStore(paths)
    index.upsert(source)
    wiki_path = paths.wiki_root / "current" / "product.md"
    wiki_path.parent.mkdir(parents=True)
    wiki_path.write_bytes(b"# current wiki\n")
    return paths, db_path, repository, index, source, raw_bytes


def _service(
    paths: ProjectPaths,
    repository: SqliteSourceRepository,
    index: SourceIndexStore,
    *,
    trash: SourceTrash | None = None,
) -> DeleteArchivedSource:
    return DeleteArchivedSource(
        paths=paths,
        sources=repository,
        index=index,
        trash=trash
        or SourceTrash(
            paths,
            now=lambda: datetime(2026, 8, 24, 12, 31, 45, 123456, tzinfo=UTC),
        ),
    )


def _command(source_id: str = "SRC-PROJECT-A-001") -> DeleteArchivedSourceInput:
    return DeleteArchivedSourceInput(
        project_id="PROJECT_A",
        source_id=source_id,
        requested_by="Owner",
        confirmed=True,
    )


@pytest.mark.parametrize("status", ["pending_ingest", "ingest_failed"])
def test_deletes_only_not_ingested_source_versions(tmp_path: Path, status: str) -> None:
    """Catches an allowed version disappearing without recoverable bytes and metadata."""
    paths, _db_path, repository, index, source, raw_bytes = _make_source(tmp_path, status=status)

    result = _service(paths, repository, index).execute(_command(source.id))

    with pytest.raises(KeyError):
        repository.get(source.id)
    active_index = json.loads(index.path.read_text(encoding="utf-8"))
    assert active_index["sources"] == []
    assert not Path(source.archive_path).parent.exists()
    assert result.trash_path.parent == paths.system_root / "trash" / "sources"
    trashed_source = result.trash_path / Path(source.archive_path).name
    assert trashed_source.read_bytes() == raw_bytes
    assert hashlib.sha256(trashed_source.read_bytes()).hexdigest() == source.sha256
    manifest = json.loads((result.trash_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["source"]["id"] == source.id
    assert manifest["source"]["ingest_status"] == status
    assert manifest["sha256"] == source.sha256


@pytest.mark.parametrize("status", ["ingesting", "ingested"])
def test_refuses_to_delete_running_or_ingested_sources(tmp_path: Path, status: str) -> None:
    """Catches a stale or tampered UI deleting material after server-side state changed."""
    paths, _db_path, repository, index, source, raw_bytes = _make_source(tmp_path, status=status)
    index_before = index.path.read_bytes()
    wiki_path = paths.wiki_root / "current" / "product.md"
    wiki_before = wiki_path.read_bytes()

    with pytest.raises(DomainError) as raised:
        _service(paths, repository, index).execute(_command(source.id))

    assert raised.value.code == "MATERIAL_DELETE_NOT_ALLOWED"
    assert repository.get(source.id) == source
    assert index.path.read_bytes() == index_before
    assert Path(source.archive_path).read_bytes() == raw_bytes
    assert wiki_path.read_bytes() == wiki_before
    assert not (paths.system_root / "trash" / "sources").exists()


def test_delete_command_rejects_ui_status_and_requires_owner_confirmation() -> None:
    """Catches a caller smuggling trusted status or omitting explicit confirmation."""
    with pytest.raises(ValidationError):
        DeleteArchivedSourceInput(
            project_id="PROJECT_A",
            source_id="SRC-PROJECT-A-001",
            requested_by="Owner",
            confirmed=True,
            ingest_status="pending_ingest",
        )
    with pytest.raises(ValidationError):
        DeleteArchivedSourceInput(
            project_id="PROJECT_A",
            source_id="SRC-PROJECT-A-001",
            requested_by="Editor",
            confirmed=True,
        )
    with pytest.raises(ValidationError):
        DeleteArchivedSourceInput(
            project_id="PROJECT_A",
            source_id="SRC-PROJECT-A-001",
            requested_by="Owner",
            confirmed=False,
        )


def test_refuses_archive_outside_canonical_source_directory(tmp_path: Path) -> None:
    """Catches a tampered DB archive path moving files outside raw/year/source-id."""
    paths, _db_path, repository, index, source, raw_bytes = _make_source(
        tmp_path, status="pending_ingest"
    )
    unsafe = paths.raw_root / "2026" / "another-source" / "material.md"
    unsafe.parent.mkdir(parents=True)
    unsafe.write_bytes(raw_bytes)
    repository.update(source.model_copy(update={"archive_path": str(unsafe)}))

    with pytest.raises(ValueError, match="MATERIAL_ARCHIVE_PATH_INVALID"):
        _service(paths, repository, index).execute(_command(source.id))

    assert repository.get(source.id).archive_path == str(unsafe)
    assert index.path.is_file()
    assert unsafe.read_bytes() == raw_bytes


class _MoveFailingTrash(SourceTrash):
    def move(self, source: SourceRecord):
        raise OSError("injected move failure")


class _RemoveAfterWriteFailingIndex(SourceIndexStore):
    def remove(self, source_id: str) -> None:
        super().remove(source_id)
        raise OSError("injected index failure")


class _DeleteFailingRepository(SqliteSourceRepository):
    def delete(self, source_id: str, project_id: str) -> None:
        raise OSError("injected database failure")


@pytest.mark.parametrize("failure", ["move", "index", "database"])
def test_restores_source_and_active_index_when_delete_transaction_fails(
    tmp_path: Path, failure: str
) -> None:
    """Catches any pre-commit failure leaving Raw or the active index partially removed."""
    paths, db_path, repository, index, source, raw_bytes = _make_source(
        tmp_path, status="pending_ingest"
    )
    index_before = index.path.read_bytes()
    active_repository: SqliteSourceRepository = repository
    active_index: SourceIndexStore = index
    trash: SourceTrash = SourceTrash(paths)
    if failure == "move":
        trash = _MoveFailingTrash(paths)
    elif failure == "index":
        active_index = _RemoveAfterWriteFailingIndex(paths)
    else:
        active_repository = _DeleteFailingRepository(db_path)

    with pytest.raises(RuntimeError, match="MATERIAL_DELETE_FAILED"):
        _service(paths, active_repository, active_index, trash=trash).execute(_command(source.id))

    assert SqliteSourceRepository(db_path).get(source.id) == source
    assert index.path.read_bytes() == index_before
    assert Path(source.archive_path).read_bytes() == raw_bytes
    trash_root = paths.system_root / "trash" / "sources"
    assert not trash_root.exists() or not list(trash_root.iterdir())
