from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

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
        authority_level=AuthorityLevel.FORMAL_EFFECTIVE,
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


def test_archive_marks_new_2_2_project_material_pending_ingest(tmp_path) -> None:
    """Catches new 2.2 project material following the legacy archived-only lifecycle."""
    from src.application.project_context import ProjectContext
    from src.application.use_cases.archive_raw_source import ArchiveRawSource
    from src.domain.models import Project
    from src.infrastructure.files.project_source_archive import ProjectSourceArchive
    from src.infrastructure.files.source_index_store import SourceIndexStore

    library = tmp_path / "library"
    paths = ProjectPaths.for_project(library, "PROJECT_A")
    paths.raw_root.mkdir(parents=True)
    paths.system_root.mkdir(parents=True)
    (paths.system_root / "project.json").write_text(
        json.dumps({"project_id": "PROJECT_A", "wiki_schema_version": "2.2"}),
        encoding="utf-8",
    )
    db_path = library / ".incubator/product_incubator.db"
    migrate(db_path)
    now = datetime(2026, 8, 12, tzinfo=UTC)
    SqliteProjectRepository(db_path).add(
        Project(
            id="PROJECT_A", name="项目 A", product_line="测试", stage="待初始化",
            current_baseline_id=None, allow_external_model=False, created_at=now, updated_at=now,
        )
    )
    context = ProjectContext("PROJECT_A", paths, db_path)
    result = ArchiveRawSource(
        paths=paths, sources=SqliteSourceRepository(db_path),
        archive_factory=lambda source_id, year: ProjectSourceArchive(
            paths=paths, source_id=source_id, year=year
        ),
        index=SourceIndexStore(paths),
        wiki_schema_version=context.wiki_schema_version,
        now=lambda: now,
    ).execute(
        _command(tmp_path / "unused.md").model_copy(
            update={
                "uploaded_name": "需求.md",
                "uploaded_bytes": "# 需求\n".encode(),
                "local_path": None,
            }
        )
    )

    assert context.wiki_schema_version == "2.2"
    assert result.ingest_status == "pending_ingest"


def test_archive_keeps_generic_schema_only_project_material_archived(tmp_path) -> None:
    """Catches generic metadata version being mistaken for the Wiki 2.2 marker."""
    from src.application.project_context import ProjectContext
    from src.application.use_cases.archive_raw_source import ArchiveRawSource
    from src.domain.models import Project
    from src.infrastructure.files.project_source_archive import ProjectSourceArchive
    from src.infrastructure.files.source_index_store import SourceIndexStore

    library = tmp_path / "library"
    paths = ProjectPaths.for_project(library, "PROJECT_A")
    paths.raw_root.mkdir(parents=True)
    paths.system_root.mkdir(parents=True)
    (paths.system_root / "project.json").write_text(
        json.dumps({"project_id": "PROJECT_A", "schema_version": "2.2"}),
        encoding="utf-8",
    )
    db_path = library / ".incubator/product_incubator.db"
    migrate(db_path)
    now = datetime(2026, 8, 12, tzinfo=UTC)
    SqliteProjectRepository(db_path).add(
        Project(
            id="PROJECT_A", name="项目 A", product_line="测试", stage="待初始化",
            current_baseline_id=None, allow_external_model=False, created_at=now, updated_at=now,
        )
    )
    context = ProjectContext("PROJECT_A", paths, db_path)
    result = ArchiveRawSource(
        paths=paths, sources=SqliteSourceRepository(db_path),
        archive_factory=lambda source_id, year: ProjectSourceArchive(
            paths=paths, source_id=source_id, year=year
        ),
        index=SourceIndexStore(paths),
        wiki_schema_version=context.wiki_schema_version,
        now=lambda: now,
    ).execute(
        _command(tmp_path / "unused.md").model_copy(
            update={
                "uploaded_name": "需求.md",
                "uploaded_bytes": "# 需求\n".encode(),
                "local_path": None,
            }
        )
    )

    assert context.wiki_schema_version == "2.1"
    assert result.ingest_status == "archived"


def test_browser_upload_archives_new_material_with_an_explicit_series(tmp_path) -> None:
    """Catches a confirmed upload not creating the material identity required for later versions."""
    from src.application.dto.materials import ArchiveRawSourceInput
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

    result = service.execute(
        ArchiveRawSourceInput(
            project_id="PROJECT_A",
            uploaded_name="需求说明.md",
            uploaded_bytes=b"# requirements\n",
            material_name="蓝领贷需求说明",
            source_type="product_requirement",
            authority_level=AuthorityLevel.FORMAL_EFFECTIVE,
            source_department="产品部",
            document_date=date(2026, 8, 12),
            material_version="v1.0",
            security_level=SecurityLevel.L2_INTERNAL,
            is_redacted_confirmed=True,
            allow_external_model=False,
        )
    )

    assert result.material_name == "蓝领贷需求说明"
    assert result.material_series_id is not None
    assert result.previous_source_id is None
    assert result.archive_path.read_bytes() == b"# requirements\n"


def test_different_filename_can_join_an_owner_selected_material_series(tmp_path) -> None:
    """Catches filename similarity being used instead of the Owner's explicit version link."""
    from src.application.dto.materials import ArchiveRawSourceInput, MaterialArchiveMode
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
    common = dict(
        project_id="PROJECT_A",
        source_type="product_requirement",
        authority_level=AuthorityLevel.FORMAL_EFFECTIVE,
        source_department="产品部",
        document_date=date(2026, 8, 12),
        security_level=SecurityLevel.L2_INTERNAL,
        is_redacted_confirmed=True,
        allow_external_model=False,
    )
    first = service.execute(
        ArchiveRawSourceInput(
            **common,
            uploaded_name="需求说明.md",
            uploaded_bytes=b"# v1\n",
            material_name="蓝领贷需求说明",
            material_version="v1.0",
        )
    )
    second = service.execute(
        ArchiveRawSourceInput(
            **common,
            uploaded_name="产品需求终稿.md",
            uploaded_bytes=b"# v2\n",
            material_name="不应采用",
            material_version="v2.0",
            archive_mode=MaterialArchiveMode.NEW_VERSION,
            target_series_id=first.material_series_id,
        )
    )

    assert second.material_series_id == first.material_series_id
    assert second.previous_source_id == first.source_id
    assert second.material_name == first.material_name
    assert first.archive_path.read_bytes() == b"# v1\n"


def test_index_failure_removes_archive_and_database_record(tmp_path) -> None:
    """Catches an index write failure leaving a material only half archived."""
    from src.application.dto.materials import ArchiveRawSourceInput
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

    class FailingIndex(SourceIndexStore):
        def upsert(self, source) -> None:
            raise OSError("disk unavailable")

    repository = SqliteSourceRepository(db_path)
    service = ArchiveRawSource(
        paths=paths,
        sources=repository,
        archive_factory=lambda source_id, year: ProjectSourceArchive(
            paths=paths, source_id=source_id, year=year
        ),
        index=FailingIndex(paths),
        now=lambda: now,
    )
    with pytest.raises(RuntimeError, match="SOURCE_ARCHIVE_COMMIT_FAILED"):
        service.execute(
            ArchiveRawSourceInput(
                project_id="PROJECT_A",
                uploaded_name="需求说明.md",
                uploaded_bytes=b"# requirements\n",
                material_name="蓝领贷需求说明",
                source_type="product_requirement",
                authority_level=AuthorityLevel.FORMAL_EFFECTIVE,
                source_department="产品部",
                document_date=date(2026, 8, 12),
                material_version="v1.0",
                security_level=SecurityLevel.L2_INTERNAL,
                is_redacted_confirmed=True,
                allow_external_model=False,
            )
        )

    assert repository.list_for_project("PROJECT_A") == []
    assert not [path for path in paths.raw_root.rglob("*") if path.is_file()]


def test_archive_rejects_external_call_when_project_has_not_authorized_it(tmp_path) -> None:
    """Catches a browser request overriding the project's external-model safety setting."""
    from src.application.dto.materials import ArchiveRawSourceInput
    from src.application.use_cases.archive_raw_source import ArchiveRawSource
    from src.infrastructure.files.project_source_archive import ProjectSourceArchive
    from src.infrastructure.files.source_index_store import SourceIndexStore

    library = tmp_path / "library"
    paths = ProjectPaths.for_project(library, "PROJECT_A")
    paths.raw_root.mkdir(parents=True)
    db_path = library / ".incubator/product_incubator.db"
    migrate(db_path)
    now = datetime(2026, 8, 12, tzinfo=UTC)
    projects = SqliteProjectRepository(db_path)
    projects.add(
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
    sources = SqliteSourceRepository(db_path)
    service = ArchiveRawSource(
        paths=paths,
        projects=projects,
        sources=sources,
        archive_factory=lambda source_id, year: ProjectSourceArchive(
            paths=paths, source_id=source_id, year=year
        ),
        index=SourceIndexStore(paths),
        now=lambda: now,
    )
    with pytest.raises(ValueError, match="EXTERNAL_CALL_DENIED"):
        service.execute(
            ArchiveRawSourceInput(
                project_id="PROJECT_A",
                uploaded_name="需求.md",
                uploaded_bytes=b"# requirements\n",
                material_name="需求说明",
                source_type="product_requirement",
                authority_level=AuthorityLevel.FORMAL_EFFECTIVE,
                source_department="产品部",
                document_date=now.date(),
                material_version="v1.0",
                security_level=SecurityLevel.L2_INTERNAL,
                is_redacted_confirmed=True,
                allow_external_model=True,
            )
        )
    assert sources.list_for_project("PROJECT_A") == []
