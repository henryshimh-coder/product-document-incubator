from __future__ import annotations

import argparse
import hashlib
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.domain.enums import BaselineStatus
from src.domain.models import Baseline, BaselineManifest, Project
from src.infrastructure.db.migrations import migrate
from src.infrastructure.db.repositories import SqliteBaselineRepository, SqliteProjectRepository
from src.infrastructure.files.manifest_store import ManifestStore
from src.infrastructure.files.markdown_store import MarkdownStore

PROJECT_ID = "LLD"
BASELINE_ID = "BASE-LLD-724_1"
BASELINE_VERSION = "LLD-724_1"
PUBLISHED_AT = datetime(2026, 7, 29, 7, 0, tzinfo=UTC)


def bootstrap(project_root: Path) -> BaselineManifest:
    """Create and verify the deterministic local demo baseline."""
    db_path = project_root / "data/local_state/product_intelligence.db"
    migrate(db_path)
    markdown_store = MarkdownStore(project_root)
    full_document_path, card_snapshot_path = markdown_store.write_baseline(
        BASELINE_VERSION,
        "# 产品智策初始基线\n\n当前版本：LLD-724_1\n\n## 目标客群\n\n仅作为脱敏演示基线使用。\n",
        [
            {
                "applicable_scope": "演示",
                "authority_level": "formal_effective",
                "card_type": "rule",
                "content": "仅作为脱敏演示基线使用。",
                "id": "RULE-LLD-001",
                "owner": "产品经理",
                "product_version": BASELINE_VERSION,
                "source_refs": ["SRC-LLD-BASE"],
                "status": "effective",
                "title": "目标客群",
            }
        ],
    )
    manifest = BaselineManifest(
        schema_version="1.0",
        project_id=PROJECT_ID,
        current_baseline_id=BASELINE_ID,
        current_version=BASELINE_VERSION,
        parent_baseline_id=None,
        full_document_path=full_document_path,
        card_snapshot_path=card_snapshot_path,
        full_document_sha256=markdown_store.sha256_for(full_document_path),
        card_snapshot_sha256=markdown_store.sha256_for(card_snapshot_path),
        change_request_id=None,
        approved_by="产品经理",
        published_at=PUBLISHED_AT,
    )
    manifest_path = project_root / "data/local_state/current_baseline.json"
    manifest_store = ManifestStore(manifest_path)
    if manifest_path.exists():
        existing = manifest_store.read_and_validate()
        _validate_manifest_assets(project_root, existing)
        manifest = existing
    else:
        manifest_store.atomic_replace(manifest)

    projects = SqliteProjectRepository(db_path)
    try:
        projects.get(PROJECT_ID)
    except KeyError:
        projects.add(
            Project(
                id=PROJECT_ID,
                name="产品智策",
                product_line="轻量交付",
                stage="demo",
                current_baseline_id=None,
                allow_external_model=False,
                created_at=PUBLISHED_AT,
                updated_at=PUBLISHED_AT,
            )
        )
    baselines = SqliteBaselineRepository(db_path)
    try:
        baselines.get(manifest.current_baseline_id)
    except KeyError:
        baselines.add(
            Baseline(
                id=manifest.current_baseline_id,
                project_id=manifest.project_id,
                version=manifest.current_version,
                parent_baseline_id=manifest.parent_baseline_id,
                status=BaselineStatus.EFFECTIVE,
                full_document_path=manifest.full_document_path,
                card_snapshot_path=manifest.card_snapshot_path,
                manifest_sha256=_sha256(manifest_path),
                change_request_id=manifest.change_request_id,
                approved_by=manifest.approved_by,
                effective_at=manifest.published_at,
                created_at=manifest.published_at,
            )
        )
    projects.update_current_baseline(PROJECT_ID, manifest.current_baseline_id)
    _validate_manifest_assets(project_root, manifest)
    _validate_sqlite_mirror(db_path, manifest)
    return manifest


def _validate_manifest_assets(project_root: Path, manifest: BaselineManifest) -> None:
    checks = (
        (manifest.full_document_path, manifest.full_document_sha256),
        (manifest.card_snapshot_path, manifest.card_snapshot_sha256),
    )
    for relative_path, expected_hash in checks:
        actual_hash = _sha256(project_root / relative_path)
        if actual_hash != expected_hash:
            raise ValueError(f"Manifest hash mismatch for {relative_path}")


def _validate_sqlite_mirror(db_path: Path, manifest: BaselineManifest) -> None:
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT current_baseline_id FROM projects WHERE id = ?", (manifest.project_id,)
        ).fetchone()
    if row is None or row[0] != manifest.current_baseline_id:
        raise ValueError("SQLite current baseline mirror does not match manifest")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    """Run demo bootstrap, optionally targeting an isolated project root."""
    parser = argparse.ArgumentParser(
        description="Initialize the product-intelligence demo baseline."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Project root to initialize (defaults to this repository root).",
    )
    arguments = parser.parse_args(argv)
    result = bootstrap(arguments.root.resolve())
    print(f"BOOTSTRAP_OK baseline={result.current_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
