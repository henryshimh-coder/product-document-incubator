from __future__ import annotations

import hashlib
import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path

from src.domain.enums import AuthorityLevel, BaselineStatus, KnowledgeStatus, SecurityLevel
from src.domain.models import Baseline, BaselineManifest, KnowledgeCard, Project, SourceRecord
from src.infrastructure.db.migrations import migrate
from src.infrastructure.db.repositories import (
    SqliteBaselineRepository,
    SqliteKnowledgeRepository,
    SqliteProjectRepository,
    SqliteSourceRepository,
)
from src.infrastructure.files.manifest_store import ManifestStore


def _migration_module():
    path = Path(__file__).resolve().parents[3] / "scripts/migrate_lld_to_v2.py"
    spec = importlib.util.spec_from_file_location("migrate_lld_to_v2", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _snapshot_tree(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.suffix not in {".db-wal", ".db-shm"}
    }


def _create_legacy_fixture(root: Path) -> None:
    now = datetime(2026, 8, 12, tzinfo=UTC)
    document = "# 蓝领贷产品方案\n\n## 产品概述\n\n迁移验证内容。\n"
    cards = [
        KnowledgeCard(
            id="RULE-LLD-001",
            project_id="LLD",
            card_type="rule",
            title="产品概述",
            content="迁移验证内容。",
            status=KnowledgeStatus.EFFECTIVE,
            product_version="LLD-724_1",
            applicable_scope="测试",
            source_refs=["SRC-LLD-BASE:SRC-LLD-BASE-0001"],
            authority_level=AuthorityLevel.FORMAL_EFFECTIVE,
            owner="产品经理",
            created_at=now,
            updated_at=now,
        )
    ]
    full_path = root / "data/obsidian_vault/02_Current_Baseline/LLD-724_1/full.md"
    cards_path = full_path.with_name("cards.json")
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full_path.write_text(document, encoding="utf-8")
    cards_path.write_text(
        json.dumps([card.model_dump(mode="json") for card in cards], ensure_ascii=False),
        encoding="utf-8",
    )
    manifest_path = root / "data/local_state/current_baseline.json"
    manifest = BaselineManifest(
        schema_version="1.0",
        project_id="LLD",
        current_baseline_id="BASE-LLD-724_1",
        current_version="LLD-724_1",
        parent_baseline_id=None,
        full_document_path=str(full_path.relative_to(root)),
        card_snapshot_path=str(cards_path.relative_to(root)),
        full_document_sha256=hashlib.sha256(document.encode()).hexdigest(),
        card_snapshot_sha256=hashlib.sha256(cards_path.read_bytes()).hexdigest(),
        change_request_id=None,
        approved_by="产品经理",
        published_at=now,
    )
    ManifestStore(manifest_path).atomic_replace(manifest)
    archive = root / "data/source_archive/LLD/SRC-LLD-BASE/当前产品方案.md"
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_text(document, encoding="utf-8")
    db_path = root / "data/local_state/product_intelligence.db"
    migrate(db_path)
    SqliteProjectRepository(db_path).add(
        Project(
            id="LLD",
            name="蓝领贷",
            product_line="信贷产品",
            stage="demo",
            current_baseline_id="BASE-LLD-724_1",
            allow_external_model=True,
            created_at=now,
            updated_at=now,
        )
    )
    SqliteSourceRepository(db_path).add(
        SourceRecord(
            id="SRC-LLD-BASE",
            project_id="LLD",
            original_filename=archive.name,
            archive_path=str(archive),
            sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
            mime_type="text/plain",
            size_bytes=archive.stat().st_size,
            source_type="formal_baseline_material",
            authority_level=AuthorityLevel.FORMAL_EFFECTIVE,
            source_department="产品部",
            provider=None,
            document_date=now.date(),
            document_version="1.0",
            applicable_baseline_version="LLD-724_1",
            security_level=SecurityLevel.L2_INTERNAL,
            is_redacted=True,
            allow_external_model=True,
            is_sandbox=False,
            ingest_status="completed",
            created_at=now,
        )
    )
    SqliteBaselineRepository(db_path).add(
        Baseline(
            id="BASE-LLD-724_1",
            project_id="LLD",
            version="LLD-724_1",
            parent_baseline_id=None,
            status=BaselineStatus.EFFECTIVE,
            full_document_path=manifest.full_document_path,
            card_snapshot_path=manifest.card_snapshot_path,
            manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            full_document_sha256=manifest.full_document_sha256,
            card_snapshot_sha256=manifest.card_snapshot_sha256,
            change_request_id=None,
            approved_by="产品经理",
            effective_at=now,
            created_at=now,
        )
    )
    SqliteKnowledgeRepository(db_path).upsert_cards(cards)


def test_lld_dry_run_writes_nothing(tmp_path: Path) -> None:
    source_root = tmp_path / "legacy"
    _create_legacy_fixture(source_root)
    library_root = tmp_path / "library"
    before = _snapshot_tree(tmp_path)

    result = _migration_module().migrate_lld(source_root, library_root, dry_run=True)

    assert result.status == "DRY_RUN_OK"
    assert _snapshot_tree(tmp_path) == before


def test_lld_migration_is_idempotent_and_preserves_raw_hashes(tmp_path: Path) -> None:
    source_root = tmp_path / "legacy"
    _create_legacy_fixture(source_root)
    library_root = tmp_path / "library"
    module = _migration_module()

    migrated = module.migrate_lld(source_root, library_root)
    repeated = module.migrate_lld(source_root, library_root)
    copied = library_root / "LLD/raw/2026/SRC-LLD-BASE/当前产品方案.md"

    assert migrated.status == "MIGRATED"
    assert repeated.status == "ALREADY_MIGRATED"
    assert copied.is_file()
    assert (
        hashlib.sha256(copied.read_bytes()).hexdigest()
        == hashlib.sha256(
            (source_root / "data/source_archive/LLD/SRC-LLD-BASE/当前产品方案.md").read_bytes()
        ).hexdigest()
    )
