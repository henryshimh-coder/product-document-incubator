from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from scripts.bootstrap_demo import bootstrap, main
from src.domain.enums import AuthorityLevel, KnowledgeStatus
from src.domain.models import BaselineManifest, KnowledgeCard
from src.infrastructure.files.manifest_store import ManifestDurabilityUncertainError, ManifestStore
from src.infrastructure.files.markdown_store import MarkdownStore


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


def _card() -> KnowledgeCard:
    return KnowledgeCard(
        id="RULE-LLD-001",
        project_id="LLD",
        card_type="rule",
        title="目标客群",
        content="脱敏后的当前规则",
        status=KnowledgeStatus.EFFECTIVE,
        product_version="LLD-724_1",
        applicable_scope="演示",
        source_refs=["SRC-LLD-BASE"],
        authority_level=AuthorityLevel.FORMAL_EFFECTIVE,
        owner="产品经理",
        confidence=None,
        created_at=datetime(2026, 7, 29, 7, 0, tzinfo=UTC),
        updated_at=datetime(2026, 7, 29, 7, 0, tzinfo=UTC),
    )


def test_markdown_store_validates_cards_on_write_and_read(tmp_path: Path) -> None:
    """Protects cards.json from invalid data that cannot be restored as domain models."""
    store = MarkdownStore(tmp_path)
    _, cards_path = store.write_baseline("LLD-724_1", "# 基线\n", [_card()])

    assert store.read_cards(cards_path) == [_card()]
    with pytest.raises(ValidationError):
        store.write_baseline("LLD-724_2", "# 基线\n", [{"id": "invalid"}])  # type: ignore[list-item]


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


def test_atomic_replace_reports_durability_uncertainty_after_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Protects callers from treating post-replace fsync failure as an unchanged Manifest."""
    store = ManifestStore(tmp_path / "current_baseline.json")
    current_manifest = _manifest("BASE-LLD-724_1", "LLD-724_1")
    candidate_manifest = _manifest("BASE-LLD-724_2", "LLD-724_2")
    store.atomic_replace(current_manifest)
    monkeypatch.setattr(
        "src.infrastructure.files.manifest_store.fsync_directory",
        Mock(side_effect=OSError("directory sync error")),
    )

    with pytest.raises(ManifestDurabilityUncertainError, match="durability"):
        store.atomic_replace(candidate_manifest)

    assert store.read_and_validate() == candidate_manifest


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


def test_bootstrap_preserves_existing_manifest_assets_without_creating_initial_vault(
    tmp_path: Path,
) -> None:
    """Protects the Manifest authority from bootstrap's initial-data writer."""
    version = "LLD-999_1"
    vault = tmp_path / "data/obsidian_vault/02_Current_Baseline" / version
    vault.mkdir(parents=True)
    full_document = vault / "full.md"
    cards = vault / "cards.json"
    full_document.write_text("# 既有权威基线\n", encoding="utf-8")
    cards.write_text("[]\n", encoding="utf-8")
    manifest = BaselineManifest(
        schema_version="1.0",
        project_id="LLD",
        current_baseline_id="BASE-LLD-999_1",
        current_version=version,
        parent_baseline_id=None,
        full_document_path=str(full_document.relative_to(tmp_path)),
        card_snapshot_path=str(cards.relative_to(tmp_path)),
        full_document_sha256=sha256(full_document.read_bytes()).hexdigest(),
        card_snapshot_sha256=sha256(cards.read_bytes()).hexdigest(),
        change_request_id=None,
        approved_by="产品经理",
        published_at=datetime(2026, 7, 29, 7, 0, tzinfo=UTC),
    )
    ManifestStore(tmp_path / "data/local_state/current_baseline.json").atomic_replace(manifest)

    assert bootstrap(tmp_path) == manifest
    assert full_document.read_text(encoding="utf-8") == "# 既有权威基线\n"
    assert cards.read_text(encoding="utf-8") == "[]\n"
    assert not (tmp_path / "data/obsidian_vault/02_Current_Baseline/LLD-724_1").exists()


@pytest.mark.parametrize(
    ("column", "invalid_value"),
    [
        ("manifest_sha256", "0" * 64),
        ("version", "LLD-incorrect"),
        ("full_document_path", "data/obsidian_vault/other/full.md"),
        ("card_snapshot_path", "data/obsidian_vault/other/cards.json"),
    ],
)
def test_bootstrap_rejects_sqlite_baseline_mirror_mismatch(
    tmp_path: Path, column: str, invalid_value: str
) -> None:
    """Protects Manifest authority from a stale or forged SQLite baseline mirror."""
    manifest = bootstrap(tmp_path)
    db_path = tmp_path / "data/local_state/product_intelligence.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            f"UPDATE baselines SET {column} = ? WHERE id = ?",  # noqa: S608
            (invalid_value, manifest.current_baseline_id),
        )

    with pytest.raises(ValueError, match="baseline mirror"):
        bootstrap(tmp_path)


def test_bootstrap_rejects_existing_project_baseline_id_mismatch_without_repair(
    tmp_path: Path,
) -> None:
    """Protects mirror corruption from being silently normalized on repeat bootstrap."""
    manifest = bootstrap(tmp_path)
    db_path = tmp_path / "data/local_state/product_intelligence.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE projects SET current_baseline_id = ? WHERE id = ?",
            ("BASE-LLD-stale", manifest.project_id),
        )

    with pytest.raises(ValueError, match="baseline mirror"):
        bootstrap(tmp_path)

    with sqlite3.connect(db_path) as connection:
        current_baseline_id = connection.execute(
            "SELECT current_baseline_id FROM projects WHERE id = ?", (manifest.project_id,)
        ).fetchone()[0]
    assert current_baseline_id == "BASE-LLD-stale"


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
