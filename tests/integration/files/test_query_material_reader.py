from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from src.domain.enums import AuthorityLevel, SecurityLevel
from src.domain.models import SourceRecord
from src.infrastructure.files.archive import SourceArchive
from src.infrastructure.files.query_material_reader import LocalQueryMaterialReader

NOW = datetime(2026, 7, 29, 7, 0, tzinfo=UTC)


def test_query_material_reader_counts_real_baseline_and_unique_archived_source_chars(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Catches fabricated safety-proof denominators or duplicate source inflation."""
    monkeypatch.chdir(tmp_path)
    baseline = tmp_path / "data/baselines/LLD-724_1/full.md"
    baseline.parent.mkdir(parents=True)
    baseline.write_text("当前基线正文", encoding="utf-8")
    archived = SourceArchive(project_id="LLD", source_id="SRC-001").save(
        "当前方案.md",
        "# 补充资料\n条款正文".encode(),
    )
    source = SourceRecord(
        id="SRC-001",
        project_id="LLD",
        original_filename="当前方案.md",
        archive_path=str(archived.path),
        sha256=archived.sha256,
        mime_type="text/markdown",
        size_bytes=archived.size_bytes,
        source_type="formal_document",
        authority_level=AuthorityLevel.FORMAL_EFFECTIVE,
        source_department="产品部",
        provider=None,
        document_date=date(2026, 7, 29),
        document_version="v1.0",
        applicable_baseline_version="LLD-724_1",
        security_level=SecurityLevel.L2_INTERNAL,
        is_redacted=True,
        allow_external_model=True,
        is_sandbox=False,
        ingest_status="completed",
        created_at=NOW,
    )

    total = LocalQueryMaterialReader(tmp_path).total_chars(
        "data/baselines/LLD-724_1/full.md",
        [source, source],
    )

    assert total == len("当前基线正文") + len("# 补充资料\n条款正文")
