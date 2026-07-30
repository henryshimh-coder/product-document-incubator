from __future__ import annotations

import importlib
from hashlib import sha256
from pathlib import Path


def archive_module():
    """Loads the archive adapter so the missing adapter is an observable RED failure."""
    return importlib.import_module("src.infrastructure.files.archive")


def test_duplicate_archive_uses_sha256_and_does_not_overwrite(tmp_path: Path) -> None:
    """Catches a repeated upload replacing immutable archived source bytes."""
    archive = archive_module().SourceArchive(
        tmp_path / "data" / "source_archive", project_id="LLD", source_id="SRC-001"
    )
    payload = "# 风险意见\n保持原始材料".encode()

    first = archive.save("风险意见.md", payload)
    second = archive.save("风险意见.md", payload)

    assert first.sha256 == second.sha256 == sha256(payload).hexdigest()
    assert first.path == second.path
    assert first.path.read_bytes() == payload
    assert first.path == tmp_path / "data" / "source_archive" / "LLD" / "SRC-001" / "风险意见.md"


def test_archive_reuses_existing_hash_without_creating_a_second_file(tmp_path: Path) -> None:
    """Catches a same-project duplicate consuming another immutable source path."""
    root = tmp_path / "data" / "source_archive"
    payload = b"same source text"

    first = (
        archive_module()
        .SourceArchive(root, project_id="LLD", source_id="SRC-001")
        .save("risk.txt", payload)
    )
    duplicate = (
        archive_module()
        .SourceArchive(root, project_id="LLD", source_id="SRC-002")
        .save("different-name.txt", payload)
    )

    assert duplicate.path == first.path
    assert list((root / "LLD").rglob("*")) == [first.path.parent, first.path]
