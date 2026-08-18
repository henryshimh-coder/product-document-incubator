from __future__ import annotations

import json
from datetime import UTC, date, datetime
from hashlib import sha256

from src.domain.enums import AuthorityLevel, DocumentGenerationMode, SecurityLevel
from src.domain.models import SourceRecord
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


def test_source_index_mirrors_wiki_ingest_result(tmp_path) -> None:
    """Catches successful ingest metadata being omitted from the project-local mirror."""
    from src.infrastructure.files.source_index_store import SourceIndexStore

    paths = ProjectPaths.for_project(tmp_path / "library", "PROJECT_A")
    ingested_at = datetime(2026, 8, 17, tzinfo=UTC)
    source = SourceRecord(
        id="SRC-001", project_id="PROJECT_A", original_filename="需求说明.md",
        archive_path="raw/2026/SRC-001/需求说明.md", sha256="a" * 64,
        mime_type="text/markdown", size_bytes=42, source_type="product_requirement",
        authority_level=AuthorityLevel.FORMAL_EFFECTIVE, source_department="产品部", provider=None,
        document_date=date(2026, 8, 17), document_version="v1.0",
        applicable_baseline_version="未关联基线", security_level=SecurityLevel.L2_INTERNAL,
        is_redacted=True, allow_external_model=True, is_sandbox=False, ingest_status="ingested",
        created_at=ingested_at, ingest_schema_version="2.2", ingested_at=ingested_at,
        source_page_path="wiki/sources/SRC-001-requirements.md",
        topic_page_paths=["wiki/topics/pricing.md"], ingest_result_digest="b" * 64,
        generation_mode=DocumentGenerationMode.EXTERNAL_AI,
    )
    store = SourceIndexStore(paths)

    store.upsert(source)

    item = json.loads(store.path.read_text(encoding="utf-8"))["sources"][0]
    assert item["ingest_status"] == "ingested"
    assert item["source_page_path"] == "wiki/sources/SRC-001-requirements.md"
    assert item["topic_page_paths"] == ["wiki/topics/pricing.md"]
    assert item["generation_mode"] == "external_ai"


def test_source_index_normalizes_2_1_entries_during_2_2_upsert(tmp_path) -> None:
    """Catches a declared 2.2 index retaining partial carried-forward 2.1 entries."""
    from src.infrastructure.files.source_index_store import SourceIndexStore

    paths = ProjectPaths.for_project(tmp_path / "library", "PROJECT_A")
    paths.system_root.mkdir(parents=True)
    index_path = paths.system_root / "source-index.json"
    index_path.write_text(
        json.dumps(
            {
                "schema_version": "2.1",
                "project_id": "PROJECT_A",
                "sources": [
                    {
                        "source_id": "SRC-LEGACY",
                        "material_name": "历史需求",
                        "material_series_id": "MAT-PROJECT_A-LEGACY",
                        "previous_source_id": None,
                        "material_version": "v1.0",
                        "filename": "历史需求.md",
                        "archive_path": "raw/2025/SRC-LEGACY/历史需求.md",
                        "sha256": "c" * 64,
                        "source_type": "product_requirement",
                        "authority_level": "formal_effective",
                        "security_level": "l2_internal",
                        "ingest_status": "archived",
                        "created_at": "2025-01-01T00:00:00+00:00",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    source = SourceRecord(
        id="SRC-NEW", project_id="PROJECT_A", original_filename="新需求.md",
        archive_path="raw/2026/SRC-NEW/新需求.md", sha256="d" * 64,
        mime_type="text/markdown", size_bytes=42, source_type="product_requirement",
        authority_level=AuthorityLevel.FORMAL_EFFECTIVE, source_department="产品部", provider=None,
        document_date=date(2026, 8, 17), document_version="v1.0",
        applicable_baseline_version="未关联基线", security_level=SecurityLevel.L2_INTERNAL,
        is_redacted=True, allow_external_model=False, is_sandbox=False,
        ingest_status="pending_ingest", created_at=datetime(2026, 8, 17, tzinfo=UTC),
    )

    SourceIndexStore(paths).upsert(source)

    payload = json.loads(index_path.read_text(encoding="utf-8"))
    legacy = next(item for item in payload["sources"] if item["source_id"] == "SRC-LEGACY")
    assert payload["schema_version"] == "2.2"
    assert {
        "ingest_schema_version",
        "ingested_at",
        "source_page_path",
        "topic_page_paths",
        "ingest_result_digest",
        "ingest_error_code",
        "generation_mode",
    } <= legacy.keys()
    assert legacy["topic_page_paths"] == []
    assert all(
        legacy[key] is None
        for key in (
            "ingest_schema_version",
            "ingested_at",
            "source_page_path",
            "ingest_result_digest",
            "ingest_error_code",
            "generation_mode",
        )
    )
