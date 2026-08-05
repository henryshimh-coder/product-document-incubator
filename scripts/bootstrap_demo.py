from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import UTC, date, datetime
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.domain.enums import AuthorityLevel, BaselineStatus, KnowledgeStatus, SecurityLevel
from src.domain.models import Baseline, BaselineManifest, KnowledgeCard, Project, SourceRecord
from src.infrastructure.db.migrations import migrate
from src.infrastructure.db.repositories import (
    SqliteBaselineRepository,
    SqliteKnowledgeRepository,
    SqliteProjectRepository,
    SqliteSourceRepository,
)
from src.infrastructure.files.extractor import extract_document_bytes
from src.infrastructure.files.manifest_store import ManifestStore
from src.infrastructure.files.markdown_store import MarkdownStore

PROJECT_ID = "LLD"
BASELINE_ID = "BASE-LLD-724_1"
BASELINE_VERSION = "LLD-724_1"
PUBLISHED_AT = datetime(2026, 7, 29, 7, 0, tzinfo=UTC)
BASE_SOURCE_ID = "SRC-LLD-BASE"
BASE_SOURCE_FILENAME = "当前产品方案.md"
RULE_CARD_ID = "RULE-LLD-001"
MARKET_CARD_ID = "MKT-LLD-001"
RULE_CARD_CONTENT = "当前目标客群是符合准入要求的存量客户。"
MARKET_CARD_CONTENT = "客户普遍接受该奖励机制。"
# 免责声明只进入附录与基线说明，不作为任何业务卡正文。
BASELINE_DISCLAIMER = "仅作为脱敏演示基线使用。"
# 出站安全证明要求查询/自检载荷不超过证据材料总字符的 25%，演示材料需要足够篇幅。
BASE_SOURCE_BACKGROUND = "\n\n".join(
    f"第{i}段方案背景说明，记录演示产品的业务口径、适用范围与假设条件。" for i in range(1, 201)
)
BASELINE_BACKGROUND = "\n\n".join(
    f"第{i}段基线说明文字，描述演示基线的背景、口径与适用范围。" for i in range(1, 61)
)
BASE_SOURCE_CONTENT = (
    "# 当前产品方案\n\n"
    "文档版本：v1.0\n\n"
    "## 目标客群\n\n"
    f"{RULE_CARD_CONTENT}\n\n"
    "## 市场判断\n\n"
    f"{MARKET_CARD_CONTENT}\n\n"
    "## 方案背景\n\n"
    f"{BASE_SOURCE_BACKGROUND}\n\n"
    "## 附录\n\n"
    f"{BASELINE_DISCLAIMER}\n"
)


def bootstrap(project_root: Path) -> BaselineManifest:
    """Create and verify the deterministic local demo baseline."""
    db_path = project_root / "data/local_state/product_intelligence.db"
    migrate(db_path)
    markdown_store = MarkdownStore(project_root)
    manifest_path = project_root / "data/local_state/current_baseline.json"
    manifest_store = ManifestStore(manifest_path)
    base_archive_path, base_sha256, base_size = _write_base_source_archive(project_root)
    base_refs = _base_source_refs(base_archive_path)
    if manifest_path.exists():
        manifest = manifest_store.read_and_validate()
        _validate_manifest_assets(project_root, manifest)
    else:
        full_document_path, card_snapshot_path = markdown_store.write_baseline(
            BASELINE_VERSION,
            (
                "# 产品智策初始基线\n\n当前版本：LLD-724_1\n\n## 目标客群\n\n"
                f"{RULE_CARD_CONTENT}\n\n## 市场判断\n\n{MARKET_CARD_CONTENT}\n\n"
                f"## 基线说明\n\n{BASELINE_BACKGROUND}\n\n## 附录\n\n{BASELINE_DISCLAIMER}\n"
            ),
            [
                KnowledgeCard(
                    id=RULE_CARD_ID,
                    project_id=PROJECT_ID,
                    card_type="rule",
                    title="目标客群",
                    content=RULE_CARD_CONTENT,
                    status=KnowledgeStatus.EFFECTIVE,
                    product_version=BASELINE_VERSION,
                    applicable_scope="演示",
                    source_refs=[base_refs[RULE_CARD_CONTENT]],
                    authority_level=AuthorityLevel.FORMAL_EFFECTIVE,
                    owner="产品经理",
                    confidence=None,
                    created_at=PUBLISHED_AT,
                    updated_at=PUBLISHED_AT,
                ),
                KnowledgeCard(
                    id=MARKET_CARD_ID,
                    project_id=PROJECT_ID,
                    card_type="market_judgment",
                    title="奖励机制接受度",
                    content=MARKET_CARD_CONTENT,
                    status=KnowledgeStatus.EFFECTIVE,
                    product_version=BASELINE_VERSION,
                    applicable_scope="演示",
                    source_refs=[base_refs[MARKET_CARD_CONTENT]],
                    authority_level=AuthorityLevel.FORMAL_EFFECTIVE,
                    owner="产品经理",
                    confidence=None,
                    created_at=PUBLISHED_AT,
                    updated_at=PUBLISHED_AT,
                ),
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
        manifest_store.atomic_replace(manifest)

    projects = SqliteProjectRepository(db_path)
    try:
        projects.get(PROJECT_ID)
    except KeyError:
        project_created = True
        projects.add(
            Project(
                id=PROJECT_ID,
                name="产品智策",
                product_line="轻量交付",
                stage="demo",
                current_baseline_id=None,
                # 演示项目允许外部模型；逐来源授权与脱敏检查仍分别把关。
                allow_external_model=True,
                created_at=PUBLISHED_AT,
                updated_at=PUBLISHED_AT,
            )
        )
    else:
        project_created = False
    _ensure_base_source_record(db_path, base_archive_path, base_sha256, base_size)
    _ensure_base_source_relations(db_path)
    baselines = SqliteBaselineRepository(db_path)
    if project_created:
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
                    full_document_sha256=manifest.full_document_sha256,
                    card_snapshot_sha256=manifest.card_snapshot_sha256,
                    change_request_id=manifest.change_request_id,
                    approved_by=manifest.approved_by,
                    effective_at=manifest.published_at,
                    created_at=manifest.published_at,
                )
            )
        projects.update_current_baseline(PROJECT_ID, manifest.current_baseline_id)
    else:
        _backfill_baseline_hashes(db_path, manifest)
        _validate_sqlite_mirror(db_path, manifest_path, manifest)
    _mirror_snapshot_cards(project_root, db_path, manifest)
    _validate_manifest_assets(project_root, manifest)
    _validate_sqlite_mirror(db_path, manifest_path, manifest)
    return manifest


def _write_base_source_archive(project_root: Path) -> tuple[str, str, int]:
    """Archive the formal base material the demo baseline cards cite."""
    archive_dir = project_root / "data/source_archive" / PROJECT_ID / BASE_SOURCE_ID
    archive_dir.mkdir(parents=True, exist_ok=True)
    payload = BASE_SOURCE_CONTENT.encode("utf-8")
    archive_path = archive_dir / BASE_SOURCE_FILENAME
    if not archive_path.exists():
        archive_path.write_bytes(payload)
    return str(archive_path), hashlib.sha256(payload).hexdigest(), len(payload)


def _base_source_refs(archive_path: str) -> dict[str, str]:
    """Locate each card's chunk inside the archived base material."""
    payload = Path(archive_path).read_bytes()
    extracted = extract_document_bytes(
        payload,
        filename=BASE_SOURCE_FILENAME,
        source_id=BASE_SOURCE_ID,
    )
    refs: dict[str, str] = {}
    for content in (RULE_CARD_CONTENT, MARKET_CARD_CONTENT):
        chunk = next((item for item in extracted.chunks if content in item.text), None)
        if chunk is None:
            raise ValueError(f"Base source archive is missing chunk for: {content}")
        refs[content] = f"{BASE_SOURCE_ID}:{chunk.chunk_id}"
    return refs


def _ensure_base_source_record(
    db_path: Path,
    archive_path: str,
    sha256: str,
    size_bytes: int,
) -> None:
    sources = SqliteSourceRepository(db_path)
    try:
        sources.get(BASE_SOURCE_ID)
    except KeyError:
        sources.add(
            SourceRecord(
                id=BASE_SOURCE_ID,
                project_id=PROJECT_ID,
                original_filename=BASE_SOURCE_FILENAME,
                archive_path=archive_path,
                sha256=sha256,
                mime_type="text/markdown",
                size_bytes=size_bytes,
                source_type="formal_baseline_material",
                authority_level=AuthorityLevel.FORMAL_EFFECTIVE,
                source_department="产品",
                provider=None,
                document_date=date(2026, 7, 29),
                document_version="v1.0",
                applicable_baseline_version=BASELINE_VERSION,
                security_level=SecurityLevel.L2_INTERNAL,
                is_redacted=True,
                allow_external_model=True,
                is_sandbox=False,
                ingest_status="completed",
                created_at=PUBLISHED_AT,
            )
        )


def _ensure_base_source_relations(db_path: Path) -> None:
    """Persist derived_from edges so traceability starts from real relations."""
    with sqlite3.connect(db_path) as connection:
        for card_id in (RULE_CARD_ID, MARKET_CARD_ID):
            relation_id = (
                "REL-"
                + hashlib.sha256(
                    "\n".join((BASE_SOURCE_ID, "derived_from", card_id)).encode("utf-8")
                )
                .hexdigest()[:16]
                .upper()
            )
            existing = connection.execute(
                "SELECT 1 FROM relations WHERE id = ?", (relation_id,)
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO relations (
                        id, project_id, source_id, relation_type, target_id,
                        source_ref, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        relation_id,
                        PROJECT_ID,
                        BASE_SOURCE_ID,
                        "derived_from",
                        card_id,
                        BASE_SOURCE_ID,
                        PUBLISHED_AT.isoformat(),
                    ),
                )


def _mirror_snapshot_cards(
    project_root: Path,
    db_path: Path,
    manifest: BaselineManifest,
) -> None:
    payload = json.loads((project_root / manifest.card_snapshot_path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Demo card snapshot is not a list")
    cards = [KnowledgeCard.model_validate(item) for item in payload]
    SqliteKnowledgeRepository(db_path).upsert_cards(cards)


def _validate_manifest_assets(project_root: Path, manifest: BaselineManifest) -> None:
    checks = (
        (manifest.full_document_path, manifest.full_document_sha256),
        (manifest.card_snapshot_path, manifest.card_snapshot_sha256),
    )
    for relative_path, expected_hash in checks:
        actual_hash = _sha256(project_root / relative_path)
        if actual_hash != expected_hash:
            raise ValueError(f"Manifest hash mismatch for {relative_path}")


def _backfill_baseline_hashes(db_path: Path, manifest: BaselineManifest) -> None:
    """Backfill asset hashes for pre-upgrade rows that predate the hash columns."""
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE baselines
            SET full_document_sha256 = ?, card_snapshot_sha256 = ?
            WHERE id = ?
              AND (full_document_sha256 IS NULL OR card_snapshot_sha256 IS NULL)
            """,
            (
                manifest.full_document_sha256,
                manifest.card_snapshot_sha256,
                manifest.current_baseline_id,
            ),
        )


def _validate_sqlite_mirror(db_path: Path, manifest_path: Path, manifest: BaselineManifest) -> None:
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            """
            SELECT
                projects.current_baseline_id,
                baselines.manifest_sha256,
                baselines.version,
                baselines.full_document_path,
                baselines.card_snapshot_path,
                baselines.full_document_sha256,
                baselines.card_snapshot_sha256
            FROM projects
            LEFT JOIN baselines ON baselines.id = projects.current_baseline_id
            WHERE projects.id = ?
            """,
            (manifest.project_id,),
        ).fetchone()
    expected = (
        manifest.current_baseline_id,
        _sha256(manifest_path),
        manifest.current_version,
        manifest.full_document_path,
        manifest.card_snapshot_path,
        manifest.full_document_sha256,
        manifest.card_snapshot_sha256,
    )
    if row is None or tuple(row) != expected:
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
