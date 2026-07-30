from __future__ import annotations

import importlib
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path

import pytest

from src.domain.errors import DomainError


def archive_module():
    """Loads the archive adapter so the missing adapter is an observable RED failure."""
    return importlib.import_module("src.infrastructure.files.archive")


def test_duplicate_archive_uses_sha256_and_does_not_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches a repeated upload replacing immutable archived source bytes."""
    monkeypatch.chdir(tmp_path)
    archive = archive_module().SourceArchive(project_id="LLD", source_id="SRC-001")
    payload = "# 风险意见\n保持原始材料".encode()

    first = archive.save("风险意见.md", payload)
    second = archive.save("风险意见.md", payload)

    assert first.sha256 == second.sha256 == sha256(payload).hexdigest()
    assert first.path == second.path
    assert first.path.read_bytes() == payload
    assert first.path == tmp_path / "data" / "source_archive" / "LLD" / "SRC-001" / "风险意见.md"


def test_archive_reuses_existing_hash_without_creating_a_second_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches a same-project duplicate consuming another immutable source path."""
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "data" / "source_archive"
    payload = b"same source text"

    first = (
        archive_module()
        .SourceArchive(project_id="LLD", source_id="SRC-001")
        .save("risk.txt", payload)
    )
    duplicate = (
        archive_module()
        .SourceArchive(project_id="LLD", source_id="SRC-002")
        .save("different-name.txt", payload)
    )

    assert duplicate.path == first.path
    assert list((root / "LLD").rglob("*")) == [first.path.parent, first.path]


def test_archive_does_not_accept_a_caller_selected_root(tmp_path: Path) -> None:
    """Catches callers redirecting immutable source storage to a lookalike directory."""
    with pytest.raises(TypeError):
        archive_module().SourceArchive(
            tmp_path / "untrusted" / "source_archive",
            project_id="LLD",
            source_id="SRC-001",
        )


def test_archive_uses_the_production_data_source_archive_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches a default archive location drifting away from data/source_archive."""
    monkeypatch.chdir(tmp_path)

    result = (
        archive_module()
        .SourceArchive(project_id="LLD", source_id="SRC-001")
        .save("risk.txt", b"default root")
    )

    assert result.path == tmp_path / "data" / "source_archive" / "LLD" / "SRC-001" / "risk.txt"


@pytest.mark.parametrize("field", ["project_id", "source_id"])
def test_archive_rejects_unsafe_business_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    """Catches a business identifier becoming an archive path traversal component."""
    monkeypatch.chdir(tmp_path)
    kwargs = {"project_id": "LLD", "source_id": "SRC-001"}
    kwargs[field] = "../escape"

    with pytest.raises(DomainError):
        archive_module().SourceArchive(**kwargs)


def test_archive_never_overwrites_conflicting_bytes_at_the_same_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches a later upload replacing immutable bytes for an occupied source filename."""
    monkeypatch.chdir(tmp_path)
    archive = archive_module().SourceArchive(project_id="LLD", source_id="SRC-001")
    archive.save("risk.txt", b"first")

    with pytest.raises(DomainError, match="ARCHIVE_PATH_EXISTS"):
        archive.save("risk.txt", b"second")

    assert (
        tmp_path / "data" / "source_archive" / "LLD" / "SRC-001" / "risk.txt"
    ).read_bytes() == b"first"


def test_concurrent_archives_create_one_payload_path_per_project_digest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catches a check-then-create race duplicating equal bytes across source IDs."""
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "data" / "source_archive"
    payload = b"concurrent source text"

    def save(index: int):
        return (
            archive_module()
            .SourceArchive(project_id="LLD", source_id=f"SRC-{index:03d}")
            .save(f"risk-{index}.txt", payload)
        )

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(save, range(12)))

    assert sum(not result.duplicate for result in results) == 1
    assert {result.path for result in results} == {results[0].path}
    assert len(list((root / "LLD").rglob("*.txt"))) == 1
