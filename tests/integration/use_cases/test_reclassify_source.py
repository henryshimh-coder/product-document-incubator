from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from src.domain.enums import AuthorityLevel, SecurityLevel
from src.domain.models import SourceRecord


def test_reclassify_changes_only_legacy_source_type(tmp_path) -> None:
    """Catches a historical reclassification altering original-file identity or content metadata."""
    from src.application.dto.materials import ReclassifySourceInput
    from src.application.use_cases.reclassify_source import ReclassifySource
    from src.domain.models import Project
    from src.infrastructure.db.migrations import migrate
    from src.infrastructure.db.repositories import SqliteProjectRepository, SqliteSourceRepository
    from src.infrastructure.files.project_library import ProjectPaths
    from src.infrastructure.files.source_index_store import SourceIndexStore

    paths = ProjectPaths.for_project(tmp_path / "library", "PROJECT_A")
    paths.raw_root.mkdir(parents=True)
    db_path = paths.library_root / ".incubator/product_incubator.db"
    migrate(db_path)
    now = datetime(2026, 8, 14, tzinfo=UTC)
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
        id="SRC-LEGACY",
        project_id="PROJECT_A",
        original_filename="历史需求.md",
        archive_path=str(paths.raw_root / "历史需求.md"),
        sha256="a" * 64,
        mime_type="text/plain",
        size_bytes=1,
        source_type="product_document",
        authority_level=AuthorityLevel.FORMAL_DECISION,
        source_department="产品",
        provider=None,
        document_date=date(2026, 8, 14),
        document_version="v1",
        applicable_baseline_version="未关联基线",
        security_level=SecurityLevel.L2_INTERNAL,
        is_redacted=True,
        allow_external_model=False,
        is_sandbox=False,
        ingest_status="archived",
        created_at=now,
    )
    (paths.raw_root / "历史需求.md").write_text("x", encoding="utf-8")
    repository = SqliteSourceRepository(db_path)
    repository.add(source)

    ReclassifySource(
        paths=paths, sources=repository, index=SourceIndexStore(paths), now=lambda: now
    ).execute(
        ReclassifySourceInput(
            project_id="PROJECT_A",
            source_id="SRC-LEGACY",
            new_source_type="risk_compliance",
            owner_name="Owner",
        )
    )

    after = repository.get("SRC-LEGACY")
    assert after.model_copy(update={"source_type": source.source_type}) == source


def test_reclassify_rolls_back_database_when_index_write_fails(tmp_path) -> None:
    """Catches a failed index update leaving the database classification changed alone."""
    from src.application.dto.materials import ReclassifySourceInput
    from src.application.use_cases.reclassify_source import ReclassifySource
    from src.domain.models import Project
    from src.infrastructure.db.migrations import migrate
    from src.infrastructure.db.repositories import SqliteProjectRepository, SqliteSourceRepository
    from src.infrastructure.files.project_library import ProjectPaths
    from src.infrastructure.files.source_index_store import SourceIndexStore

    paths = ProjectPaths.for_project(tmp_path / "library", "PROJECT_A")
    paths.raw_root.mkdir(parents=True)
    db_path = paths.library_root / ".incubator/product_incubator.db"
    migrate(db_path)
    now = datetime(2026, 8, 14, tzinfo=UTC)
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
    before = SourceRecord(
        id="SRC-LEGACY",
        project_id="PROJECT_A",
        original_filename="历史需求.md",
        archive_path=str(paths.raw_root / "历史需求.md"),
        sha256="a" * 64,
        mime_type="text/plain",
        size_bytes=1,
        source_type="product_document",
        authority_level=AuthorityLevel.FORMAL_DECISION,
        source_department="产品",
        provider=None,
        document_date=date(2026, 8, 14),
        document_version="v1",
        applicable_baseline_version="未关联基线",
        security_level=SecurityLevel.L2_INTERNAL,
        is_redacted=True,
        allow_external_model=False,
        is_sandbox=False,
        ingest_status="archived",
        created_at=now,
    )
    repository = SqliteSourceRepository(db_path)
    repository.add(before)

    class FailingIndex(SourceIndexStore):
        def upsert(self, source) -> None:
            raise OSError("index unavailable")

    with pytest.raises(RuntimeError, match="SOURCE_RECLASSIFY_FAILED"):
        ReclassifySource(paths=paths, sources=repository, index=FailingIndex(paths)).execute(
            ReclassifySourceInput(
                project_id="PROJECT_A",
                source_id="SRC-LEGACY",
                new_source_type="risk_compliance",
                owner_name="Owner",
            )
        )
    assert repository.get("SRC-LEGACY") == before
