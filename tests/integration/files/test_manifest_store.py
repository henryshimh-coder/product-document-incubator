from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

import pytest

from scripts.bootstrap_demo import bootstrap, main
from src.domain.models import BaselineManifest
from src.infrastructure.files.manifest_store import ManifestStore


def _manifest(baseline_id: str, version: str) -> BaselineManifest:
    return BaselineManifest(
        schema_version="1.0",
        project_id="LLD",
        current_baseline_id=baseline_id,
        current_version=version,
        parent_baseline_id=None,
        full_document_path=f"data/obsidian_vault/02_Current_Baseline/{version}/full.md",
        card_snapshot_path=f"data/obsidian_vault/02_Current_Baseline/{version}/cards.json",
        full_document_sha256="a" * 64,
        card_snapshot_sha256="b" * 64,
        change_request_id=None,
        approved_by="产品经理",
        published_at=datetime(2026, 7, 29, 7, 0, tzinfo=UTC),
    )


def test_atomic_replace_keeps_old_manifest_when_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Protects the only current-baseline authority from a failed write."""
    store = ManifestStore(tmp_path / "current_baseline.json")
    current_manifest = _manifest("BASE-LLD-724_1", "LLD-724_1")
    candidate_manifest = _manifest("BASE-LLD-724_2", "LLD-724_2")
    store.atomic_replace(current_manifest)
    monkeypatch.setattr(os, "replace", Mock(side_effect=OSError("disk error")))

    with pytest.raises(OSError, match="disk error"):
        store.atomic_replace(candidate_manifest)

    assert store.read_and_validate() == current_manifest
    assert not store.path.with_name("current_baseline.json.tmp").exists()


def test_read_and_validate_rejects_invalid_manifest(tmp_path: Path) -> None:
    """Protects callers from treating malformed JSON as an effective baseline."""
    path = tmp_path / "current_baseline.json"
    path.write_text('{"schema_version": "1.0"}', encoding="utf-8")

    with pytest.raises(ValueError):
        ManifestStore(path).read_and_validate()


def test_bootstrap_creates_a_verified_baseline_and_is_repeatable(tmp_path: Path) -> None:
    """Protects demo startup from diverging vault, manifest, and SQLite mirrors."""
    first = bootstrap(tmp_path)
    second = bootstrap(tmp_path)
    store = ManifestStore(tmp_path / "data/local_state/current_baseline.json")

    assert first == second == store.read_and_validate()
    assert first.current_baseline_id == "BASE-LLD-724_1"
    assert (tmp_path / first.full_document_path).is_file()
    assert (tmp_path / first.card_snapshot_path).is_file()

    import sqlite3

    with sqlite3.connect(tmp_path / "data/local_state/product_intelligence.db") as connection:
        mirror = connection.execute(
            "SELECT current_baseline_id FROM projects WHERE id = ?", ("LLD",)
        ).fetchone()[0]
        baseline_count = connection.execute("SELECT COUNT(*) FROM baselines").fetchone()[0]
    assert mirror == first.current_baseline_id
    assert baseline_count == 1


def test_bootstrap_cli_honors_explicit_root_without_touching_repository_data(
    tmp_path: Path,
) -> None:
    """Protects test and CI isolation while retaining an explicit CLI root."""
    project_root = Path(__file__).resolve().parents[3]
    repository_paths = (
        project_root / "data/local_state/current_baseline.json",
        project_root / "data/local_state/product_intelligence.db",
        project_root / "data/obsidian_vault/02_Current_Baseline/LLD-724_1/full.md",
        project_root / "data/obsidian_vault/02_Current_Baseline/LLD-724_1/cards.json",
    )
    before = {path: path.read_bytes() if path.exists() else None for path in repository_paths}

    assert main(["--root", str(tmp_path)]) == 0
    assert (tmp_path / "data/local_state/current_baseline.json").is_file()
    assert (tmp_path / "data/obsidian_vault/02_Current_Baseline/LLD-724_1/full.md").is_file()
    after = {path: path.read_bytes() if path.exists() else None for path in repository_paths}
    assert after == before


def test_bootstrap_script_runs_directly_with_an_explicit_temp_root(tmp_path: Path) -> None:
    """Protects the documented direct-script entrypoint and import-path regression."""
    project_root = Path(__file__).resolve().parents[3]

    result = subprocess.run(
        [sys.executable, "scripts/bootstrap_demo.py", "--root", str(tmp_path)],
        cwd=project_root,
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "BOOTSTRAP_OK baseline=LLD-724_1" in result.stdout
    assert (tmp_path / "data/local_state/current_baseline.json").is_file()
