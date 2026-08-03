from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from src.domain.enums import AuthorityLevel, SecurityLevel
from src.domain.errors import DomainError
from src.domain.models import SourceRecord
from src.infrastructure.files.archive import SourceArchive
from src.infrastructure.files.query_material_reader import LocalQueryMaterialReader

NOW = datetime(2026, 7, 29, 7, 0, tzinfo=UTC)


def _source(path: Path, sha256: str, size_bytes: int, *, source_id: str = "SRC-001"):
    return SourceRecord(
        id=source_id,
        project_id="LLD",
        original_filename=path.name,
        archive_path=str(path),
        sha256=sha256,
        mime_type="text/markdown",
        size_bytes=size_bytes,
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


def _baseline(
    tmp_path: Path, text: str = "# 当前方案\n## 目标客群\n当前基线正文"
) -> tuple[Path, str]:
    path = tmp_path / "data/obsidian_vault/02_Current_Baseline/LLD-724_1/full.md"
    path.parent.mkdir(parents=True)
    payload = text.encode()
    path.write_bytes(payload)
    return path, hashlib.sha256(payload).hexdigest()


def test_reader_verifies_real_baseline_and_archive_then_counts_unique_supporting_materials(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Catches fabricated denominators or duplicate counting outside verified materials."""
    monkeypatch.chdir(tmp_path)
    baseline_path, baseline_hash = _baseline(tmp_path)
    archived = SourceArchive(project_id="LLD", source_id="SRC-001").save(
        "当前方案.md",
        "# 补充资料\n条款正文".encode(),
    )
    reader = LocalQueryMaterialReader(tmp_path)

    baseline = reader.read_baseline(
        project_id="LLD",
        asset_id="BASE-LLD-724_1",
        version="LLD-724_1",
        relative_path=str(baseline_path.relative_to(tmp_path)),
        expected_sha256=baseline_hash,
    )
    source = reader.read_source(_source(archived.path, archived.sha256, archived.size_bytes))

    assert baseline.filename == "full.md"
    assert any(fragment.locator.endswith("line:3") for fragment in baseline.fragments)
    assert source.filename == "当前方案.md"
    assert source.sha256 == archived.sha256
    assert reader.total_chars([baseline, source, source]) == len(baseline.text) + len(source.text)


def test_reader_rejects_baseline_hash_drift_before_using_text(
    tmp_path: Path,
) -> None:
    """Catches a changed baseline document being counted or cited under an old Manifest hash."""
    baseline_path, _ = _baseline(tmp_path)

    with pytest.raises(DomainError, match="BASELINE_INTEGRITY_FAILED"):
        LocalQueryMaterialReader(tmp_path).read_baseline(
            project_id="LLD",
            asset_id="BASE-LLD-724_1",
            version="LLD-724_1",
            relative_path=str(baseline_path.relative_to(tmp_path)),
            expected_sha256="f" * 64,
        )


@pytest.mark.parametrize(
    "relative_path",
    [
        "../outside/full.md",
        "data/obsidian_vault/02_Current_Baseline_evil/LLD-724_1/full.md",
        "data/obsidian_vault/02_Current_Baseline/OTHER/full.md",
    ],
)
def test_reader_rejects_baseline_traversal_lookalike_or_wrong_version_path(
    tmp_path: Path,
    relative_path: str,
) -> None:
    """Catches a hash-matching file outside the formal baseline asset path."""
    payload = "外部伪装基线".encode()
    target = (tmp_path / relative_path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)

    with pytest.raises(DomainError, match="BASELINE_INTEGRITY_FAILED"):
        LocalQueryMaterialReader(tmp_path).read_baseline(
            project_id="LLD",
            asset_id="BASE-LLD-724_1",
            version="LLD-724_1",
            relative_path=relative_path,
            expected_sha256=hashlib.sha256(payload).hexdigest(),
        )


def test_reader_rejects_archive_hash_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Catches SQLite sha metadata being trusted after archived bytes changed."""
    monkeypatch.chdir(tmp_path)
    archived = SourceArchive(project_id="LLD", source_id="SRC-001").save(
        "当前方案.md",
        "原始可信正文".encode(),
    )
    source = _source(archived.path, archived.sha256, archived.size_bytes)
    archived.path.write_text("已被篡改的正文", encoding="utf-8")

    with pytest.raises(DomainError, match="CITATION_INVALID"):
        LocalQueryMaterialReader(tmp_path).read_source(source)


@pytest.mark.parametrize("variant", ["lookalike", "traversal"])
def test_reader_rejects_archive_path_outside_exact_project_source_root(
    tmp_path: Path,
    variant: str,
) -> None:
    """Catches source_archive lookalikes and traversal paths reaching local file reads."""
    if variant == "lookalike":
        path = tmp_path / "data/source_archive_evil/LLD/SRC-001/当前方案.md"
    else:
        outside = tmp_path / "data/outside/当前方案.md"
        path = tmp_path / "data/source_archive/LLD/SRC-001/../../../outside/当前方案.md"
        outside.parent.mkdir(parents=True, exist_ok=True)
    resolved = path.resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    payload = "伪装归档".encode()
    resolved.write_bytes(payload)
    source = _source(path, hashlib.sha256(payload).hexdigest(), len(payload))

    with pytest.raises(DomainError, match="CITATION_INVALID"):
        LocalQueryMaterialReader(tmp_path).read_source(source)
