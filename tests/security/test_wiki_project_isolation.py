from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime

import pytest

from src.application.dto.wiki_ingest import IngestArchivedSourceInput
from src.domain.enums import AuthorityLevel, SecurityLevel
from src.domain.errors import DomainError
from src.domain.models import Project, SourceRecord
from src.infrastructure.db.repositories import SqliteProjectRepository, SqliteSourceRepository
from tests.integration.use_cases.test_wiki_ingest import make_ingest_fixture


def test_ingest_rejects_source_from_other_project(tmp_path) -> None:
    """Catches central source lookup crossing the active project's file boundary."""
    fixture = make_ingest_fixture(tmp_path)
    project_b_root = tmp_path / "library" / "PROJECT_B"
    raw_path = project_b_root / "raw" / "2026" / "SRC-PROJECT-B-001" / "secret.md"
    raw_path.parent.mkdir(parents=True)
    raw_path.write_text("PROJECT-B-SECRET", encoding="utf-8")
    SqliteProjectRepository(fixture.db_path).add(
        Project(
            id="PROJECT_B",
            name="Project B",
            product_line="Test",
            stage="incubating",
            current_baseline_id=None,
            allow_external_model=True,
            created_at=datetime(2026, 8, 17, tzinfo=UTC),
            updated_at=datetime(2026, 8, 17, tzinfo=UTC),
            project_root_path=str(project_b_root),
        )
    )
    source_b = SourceRecord(
        id="SRC-PROJECT-B-001",
        project_id="PROJECT_B",
        original_filename="secret.md",
        archive_path=str(raw_path),
        sha256=hashlib.sha256(raw_path.read_bytes()).hexdigest(),
        mime_type="text/plain",
        size_bytes=raw_path.stat().st_size,
        source_type="product_requirement",
        authority_level=AuthorityLevel.FORMAL_EFFECTIVE,
        source_department="Product",
        provider=None,
        document_date=date(2026, 8, 17),
        document_version="1.0",
        applicable_baseline_version="BASE-1",
        security_level=SecurityLevel.L2_INTERNAL,
        is_redacted=True,
        allow_external_model=True,
        is_sandbox=False,
        ingest_status="pending_ingest",
        created_at=datetime(2026, 8, 17, tzinfo=UTC),
        material_name="Project B secret",
        material_series_id="MAT-B",
    )
    SqliteSourceRepository(fixture.db_path).add(source_b)
    before = hashlib.sha256(raw_path.read_bytes()).hexdigest()

    with pytest.raises(DomainError, match="WIKI_CHANGESET_INVALID"):
        fixture.service.execute(
            IngestArchivedSourceInput(
                project_id="PROJECT_A",
                source_id=source_b.id,
                requested_by="Owner",
            )
        )

    assert hashlib.sha256(raw_path.read_bytes()).hexdigest() == before
    assert "PROJECT-B-SECRET" not in fixture.page("wiki/index.md").read_text()
    assert fixture.gateway.calls == []


@pytest.mark.parametrize("security", [SecurityLevel.L3_CONFIDENTIAL, SecurityLevel.L4_RESTRICTED])
def test_sensitive_source_never_reaches_gateway(tmp_path, security) -> None:
    """Catches an L3/L4 branch accidentally reusing the external Wiki workflow."""
    fixture = make_ingest_fixture(tmp_path)
    repository = SqliteSourceRepository(fixture.db_path)
    source = repository.get(fixture.source_id)
    repository.update(source.model_copy(update={"security_level": security}))

    with pytest.raises(DomainError, match="WIKI_EXTERNAL_CALL_DENIED"):
        fixture.execute()

    assert fixture.gateway.calls == []
    failed = repository.get(fixture.source_id)
    assert failed.ingest_status == "ingest_failed"
    assert failed.ingest_error_code == "WIKI_EXTERNAL_CALL_DENIED"
