from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from src.application.dto.documents import ArchiveRawSourceInput
from src.domain.enums import AuthorityLevel, SecurityLevel
from src.infrastructure.db.migrations import migrate
from src.infrastructure.db.repositories import SqliteProjectRepository, SqliteSourceRepository
from src.infrastructure.files.project_library import ProjectPaths


def _command(path: Path) -> ArchiveRawSourceInput:
    return ArchiveRawSourceInput(
        project_id="PROJECT_A",
        local_path=path,
        source_type="product_requirement",
        authority_level=AuthorityLevel.FORMAL_DECISION,
        source_department="产品部",
        document_date=date(2026, 8, 12),
        document_version="v1.0",
        security_level=SecurityLevel.L2_INTERNAL,
        is_redacted_confirmed=True,
        allow_external_model=False,
    )


def test_same_hash_in_same_project_returns_existing_source(tmp_path) -> None:
    """Catches duplicate material creating a second source record in the same project."""
    from src.application.use_cases.archive_raw_source import ArchiveRawSource
    from src.infrastructure.files.project_source_archive import ProjectSourceArchive
    from src.infrastructure.files.source_index_store import SourceIndexStore

    library = tmp_path / "library"
    paths = ProjectPaths.for_project(library, "PROJECT_A")
    paths.raw_root.mkdir(parents=True)
    db_path = library / ".incubator/product_incubator.db"
    migrate(db_path)
    now = datetime(2026, 8, 12, tzinfo=UTC)
    SqliteProjectRepository(db_path).add(
        __import__("src.domain.models", fromlist=["Project"]).Project(
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
    service = ArchiveRawSource(
        paths=paths,
        sources=SqliteSourceRepository(db_path),
        archive_factory=lambda source_id, year: ProjectSourceArchive(
            paths=paths, source_id=source_id, year=year
        ),
        index=SourceIndexStore(paths),
        now=lambda: now,
    )
    source = tmp_path / "需求.md"
    source.write_text("# 需求\n", encoding="utf-8")

    first = service.execute(_command(source))
    second = service.execute(_command(source))

    assert second.duplicate is True
    assert second.source_id == first.source_id
