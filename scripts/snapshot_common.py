"""T12 演示快照共享原语：SnapshotManifest、捕获、恢复、校验与冻结缓存。

快照内容（相对 project_root 的四个显式目标，恢复时只允许覆盖它们）：

- ``data/local_state/product_intelligence.db``
- ``data/local_state/current_baseline.json``
- ``data/local_state/cache/``
- ``data/obsidian_vault/``

`data/source_archive/` 中的正式原始资料永远不被重置删除；恢复只重写这四个目标，
并把快照数据库中的来源归档绝对路径改写为目标 project_root 下的对应路径
（归档内容逐字节不变，SHA-256 校验保持有效）。

快照布局：``<快照目录>/manifest.json`` 记录四个目标的内容哈希，
``<快照目录>/payload/`` 保存目标字节。载荷数据库中的来源归档路径统一规范化
为 project_root 相对形式（``data/source_archive/...``），与构建机器上的绝对
路径无关，同一演示内容跨构建环境字节一致；恢复时再改写为目标根的绝对路径。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import sys
import tomllib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.bootstrap_demo import (  # noqa: E402
    BASE_SOURCE_FILENAME,
    BASE_SOURCE_ID,
    BASELINE_ID,
    BASELINE_VERSION,
    PROJECT_ID,
    RULE_CARD_CONTENT,
    RULE_CARD_ID,
)
from scripts.demo_materials import (  # noqa: E402
    DEMO_QUESTION,
    RISK_OPINION_FILENAME,
    RISK_SENTENCE,
)
from src.infrastructure.cache.ai_cache import (  # noqa: E402
    CURRENT_OUTPUT_SCHEMAS,
    AiCache,
    CacheIdentity,
    build_cache_key,
)
from src.infrastructure.db.connection import connect  # noqa: E402
from src.infrastructure.files.extractor import extract_document_bytes  # noqa: E402
from src.infrastructure.files.manifest_store import ManifestStore  # noqa: E402
from src.infrastructure.gateways.schemas import (  # noqa: E402
    IngestWorkflowOutput,
    LintWorkflowOutput,
    QueryWorkflowOutput,
)

DATABASE_REL = Path("data/local_state/product_intelligence.db")
MANIFEST_REL = Path("data/local_state/current_baseline.json")
CACHE_DIR_REL = Path("data/local_state/cache")
VAULT_DIR_REL = Path("data/obsidian_vault")
SNAPSHOT_TARGETS: tuple[Path, ...] = (DATABASE_REL, MANIFEST_REL, CACHE_DIR_REL, VAULT_DIR_REL)
SOURCE_ARCHIVE_REL = Path("data/source_archive")

SNAPSHOT_SCHEMA_VERSION = "1.0"
PAYLOAD_DIRNAME = "payload"
MANIFEST_FILENAME = "manifest.json"


class SnapshotManifest(BaseModel):
    """快照清单：记录四个目标的内容哈希，恢复前必须先核对载荷。"""

    app_version: str
    schema_version: str
    baseline_version: str
    database_sha256: str
    manifest_sha256: str
    vault_sha256: str
    cache_index_sha256: str
    created_at: datetime


@dataclass
class ValidationReport:
    """`validate_data` 的结构化结果，ok 为 True 时演示环境可用。"""

    ok: bool
    baseline_version: str | None
    checks: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _directory_sha256(root: Path) -> str:
    """目录内容的确定性摘要：排序后的 相对路径:文件哈希 列表再取哈希。"""
    entries: list[str] = []
    if root.exists():
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            if path.name == ".DS_Store":
                continue
            entries.append(f"{path.relative_to(root)}:{_sha256(path)}")
    return hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest()


def app_version(repo_root: Path | None = None) -> str:
    """应用版本取自 pyproject.toml，缺失时回退字面量。"""
    root = repo_root or Path(__file__).resolve().parents[1]
    try:
        document = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        return str(document["project"]["version"])
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        return "0.1.0"


def validate_data(
    project_root: Path,
    expected_baseline: str = BASELINE_VERSION,
) -> ValidationReport:
    """校验演示环境：Manifest、基线资产、SQLite 镜像、缓存与来源归档。"""
    project_root = project_root.resolve()
    report = ValidationReport(ok=False, baseline_version=None)
    manifest_store = ManifestStore(project_root / MANIFEST_REL)
    try:
        manifest = manifest_store.read_and_validate()
    except (ValueError, OSError) as error:
        report.errors.append(f"MANIFEST_INVALID:{type(error).__name__}")
        return report
    report.baseline_version = manifest.current_version
    report.checks.append("manifest_parse")
    if manifest.current_version != expected_baseline:
        report.errors.append(f"BASELINE_VERSION_MISMATCH:{manifest.current_version}")
        return report
    if manifest.current_baseline_id != BASELINE_ID:
        report.errors.append(f"BASELINE_ID_MISMATCH:{manifest.current_baseline_id}")
        return report
    report.checks.append("baseline_version")
    for relative_path, expected_hash in (
        (manifest.full_document_path, manifest.full_document_sha256),
        (manifest.card_snapshot_path, manifest.card_snapshot_sha256),
    ):
        asset = project_root / relative_path
        if not asset.is_file() or _sha256(asset) != expected_hash:
            report.errors.append(f"BASELINE_ASSET_MISMATCH:{relative_path}")
            return report
    report.checks.append("baseline_assets")
    db_path = project_root / DATABASE_REL
    if not db_path.is_file():
        report.errors.append("DATABASE_MISSING")
        return report
    with connect(db_path) as connection:
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
            (PROJECT_ID,),
        ).fetchone()
    expected_row = (
        manifest.current_baseline_id,
        _sha256(project_root / MANIFEST_REL),
        manifest.current_version,
        manifest.full_document_path,
        manifest.card_snapshot_path,
        manifest.full_document_sha256,
        manifest.card_snapshot_sha256,
    )
    if row is None or tuple(row) != expected_row:
        report.errors.append("SQLITE_MIRROR_MISMATCH")
        return report
    report.checks.append("sqlite_mirror")
    cache_error = _validate_cache(project_root, db_path)
    if cache_error is not None:
        report.errors.append(cache_error)
        return report
    report.checks.append("cache_integrity")
    archive_error = _validate_source_archives(project_root, db_path)
    if archive_error is not None:
        report.errors.append(archive_error)
        return report
    report.checks.append("source_archives")
    report.ok = True
    return report


def _validate_cache(project_root: Path, db_path: Path) -> str | None:
    """逐条核对缓存：文件 SHA、规范化 JSON 与输出 schema，键可按身份重算。"""
    cache_dir = project_root / CACHE_DIR_REL
    with connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT cache_key, task_type, source_sha256, baseline_version,
                   prompt_version, model_label, schema_version,
                   response_json, response_sha256
            FROM cache_entries ORDER BY cache_key
            """
        ).fetchall()
    for row in rows:
        cache_file = cache_dir / f"{row['cache_key']}.json"
        try:
            payload_bytes = cache_file.read_bytes()
        except OSError:
            return f"CACHE_FILE_MISSING:{row['cache_key']}"
        if hashlib.sha256(payload_bytes).hexdigest() != row["response_sha256"]:
            return f"CACHE_FILE_MISMATCH:{row['cache_key']}"
        try:
            response_text = payload_bytes.decode("utf-8")
            value = json.loads(response_text)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return f"CACHE_FILE_INVALID:{row['cache_key']}"
        canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if canonical != response_text or response_text != row["response_json"]:
            return f"CACHE_INDEX_MISMATCH:{row['cache_key']}"
        schema = CURRENT_OUTPUT_SCHEMAS.get(row["task_type"])
        if schema is None:
            return f"CACHE_TASK_UNKNOWN:{row['cache_key']}"
        try:
            schema.model_validate(value)
        except ValueError:
            return f"CACHE_SCHEMA_MISMATCH:{row['cache_key']}"
        # question 不落库；无问题的任务类型可按身份字段完整重算键。
        if row["task_type"] in {"ingest", "lint"}:
            rebuilt = build_cache_key(
                row["task_type"],
                row["source_sha256"],
                row["baseline_version"],
                row["prompt_version"],
                row["model_label"],
                row["schema_version"],
            )
            if rebuilt != row["cache_key"]:
                return f"CACHE_KEY_MISMATCH:{row['cache_key']}"
    return None


def _validate_source_archives(project_root: Path, db_path: Path) -> str | None:
    """正式原始资料必须留在受控归档目录内且内容哈希一致。"""
    with connect(db_path) as connection:
        rows = connection.execute(
            "SELECT id, archive_path, sha256, size_bytes FROM source_records ORDER BY id"
        ).fetchall()
    for row in rows:
        archive = Path(row["archive_path"])
        try:
            archive.resolve().relative_to(project_root)
        except ValueError:
            return f"ARCHIVE_PATH_ESCAPE:{row['id']}"
        if not archive.is_file():
            return f"ARCHIVE_MISSING:{row['id']}"
        if _sha256(archive) != row["sha256"] or archive.stat().st_size != row["size_bytes"]:
            return f"ARCHIVE_MISMATCH:{row['id']}"
    return None


def capture_snapshot(
    project_root: Path,
    snapshot_dir: Path,
    *,
    expected_baseline: str = BASELINE_VERSION,
) -> SnapshotManifest:
    """把演示环境的四个显式目标捕获为可校验快照。

    捕获前先运行 `validate_data`：损坏的环境不允许生成正式快照。
    清单哈希以快照目录内的载荷副本为准，保证清单描述的就是入库字节。
    """
    project_root = project_root.resolve()
    report = validate_data(project_root, expected_baseline)
    if not report.ok:
        raise ValueError(f"REFUSE_TO_SNAPSHOT_INVALID_STATE:{report.errors}")
    # WAL 模式下近期提交可能只存在于 -wal 侧车文件；复制主库文件前必须落盘。
    with connect(project_root / DATABASE_REL) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    snapshot_dir = snapshot_dir.resolve()
    payload_root = snapshot_dir / PAYLOAD_DIRNAME
    if snapshot_dir.exists():
        shutil.rmtree(snapshot_dir)
    payload_root.mkdir(parents=True)
    for relative in SNAPSHOT_TARGETS:
        source = project_root / relative
        target = payload_root / relative
        if source.is_dir():
            shutil.copytree(source, target, ignore=shutil.ignore_patterns(".DS_Store"))
        elif source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        else:
            # 缓存目录在无任何缓存时可能不存在：以空目录落盘，恢复语义一致。
            target.mkdir(parents=True, exist_ok=True)
    _normalize_archive_paths(payload_root / DATABASE_REL)
    snapshot = SnapshotManifest(
        app_version=app_version(),
        schema_version=SNAPSHOT_SCHEMA_VERSION,
        baseline_version=expected_baseline,
        database_sha256=_sha256(payload_root / DATABASE_REL),
        manifest_sha256=_sha256(payload_root / MANIFEST_REL),
        vault_sha256=_directory_sha256(payload_root / VAULT_DIR_REL),
        cache_index_sha256=_directory_sha256(payload_root / CACHE_DIR_REL),
        created_at=datetime.now(UTC),
    )
    (snapshot_dir / MANIFEST_FILENAME).write_text(
        json.dumps(snapshot.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return snapshot


def verify_snapshot_payload(snapshot_dir: Path) -> SnapshotManifest:
    """读取快照清单并核对载荷哈希；任何不符都 fail closed。"""
    snapshot_dir = snapshot_dir.resolve()
    manifest_path = snapshot_dir / MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise ValueError(f"SNAPSHOT_MANIFEST_MISSING:{snapshot_dir}")
    snapshot = SnapshotManifest.model_validate(
        json.loads(manifest_path.read_text(encoding="utf-8"))
    )
    payload_root = snapshot_dir / PAYLOAD_DIRNAME
    mismatches: list[str] = []
    if _sha256(payload_root / DATABASE_REL) != snapshot.database_sha256:
        mismatches.append("database")
    if _sha256(payload_root / MANIFEST_REL) != snapshot.manifest_sha256:
        mismatches.append("manifest")
    if _directory_sha256(payload_root / VAULT_DIR_REL) != snapshot.vault_sha256:
        mismatches.append("vault")
    if _directory_sha256(payload_root / CACHE_DIR_REL) != snapshot.cache_index_sha256:
        mismatches.append("cache_index")
    if mismatches:
        raise ValueError(f"SNAPSHOT_PAYLOAD_MISMATCH:{','.join(mismatches)}")
    return snapshot


def restore_snapshot(snapshot_dir: Path, project_root: Path) -> ValidationReport:
    """安全恢复：只覆盖四个显式目标，绝不触碰 `data/source_archive/`。

    恢复后自动运行 `validate_data`；即使校验失败，也只影响这四个目标。
    """
    project_root = project_root.resolve()
    if not project_root.is_dir():
        raise ValueError(f"RESET_ROOT_MISSING:{project_root}")
    snapshot = verify_snapshot_payload(snapshot_dir)
    payload_root = snapshot_dir.resolve() / PAYLOAD_DIRNAME
    for relative in SNAPSHOT_TARGETS:
        source = payload_root / relative
        target = project_root / relative
        if target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
        if relative == DATABASE_REL:
            # 残留的 WAL 侧车文件会被重放到新库文件上，必须与主库一并替换。
            for sidecar in (
                target.with_suffix(target.suffix + "-wal"),
                target.with_suffix(target.suffix + "-shm"),
            ):
                sidecar.unlink(missing_ok=True)
        if source.is_dir():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, target)
        elif source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        else:
            target.mkdir(parents=True, exist_ok=True)
    _rewrite_archive_paths(project_root / DATABASE_REL, project_root)
    return validate_data(project_root, snapshot.baseline_version)


def _normalize_archive_paths(db_path: Path) -> None:
    """把快照载荷数据库中的来源归档路径规范化为 project_root 相对形式。

    正式原始资料只允许放在 ``data/source_archive/`` 下；规范化后快照数据库与
    构建机器上的绝对路径无关，同一演示内容跨构建环境字节一致。无法规范化的
    路径 fail closed。UPDATE 会在页内留下与原始路径长度相关的碎片，因此
    VACUUM 重建物理布局并强制 WAL 落盘，载荷目录不留侧车文件。
    """
    marker = f"/{SOURCE_ARCHIVE_REL}/"
    relative_prefix = f"{SOURCE_ARCHIVE_REL}/"
    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute("SELECT id, archive_path FROM source_records").fetchall()
        for source_id, stored_path in rows:
            if marker in stored_path:
                rewritten = relative_prefix + stored_path.split(marker, 1)[1]
            elif stored_path.startswith(relative_prefix):
                rewritten = stored_path
            else:
                raise ValueError(f"ARCHIVE_PATH_UNNORMALIZABLE:{source_id}")
            if rewritten != stored_path:
                connection.execute(
                    "UPDATE source_records SET archive_path = ? WHERE id = ?",
                    (rewritten, source_id),
                )
        connection.commit()
        connection.execute("VACUUM")
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        connection.close()


def _rewrite_archive_paths(db_path: Path, project_root: Path) -> None:
    """把快照数据库中的来源归档路径改写为目标 project_root 下的绝对路径。"""
    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute("SELECT id, archive_path FROM source_records").fetchall()
        for source_id, stored_path in rows:
            marker = f"/{SOURCE_ARCHIVE_REL}/"
            if marker in stored_path:
                rewritten = str(project_root) + stored_path[stored_path.index(marker) :]
            elif stored_path.startswith(f"{SOURCE_ARCHIVE_REL}/"):
                rewritten = str(project_root / stored_path)
            else:
                raise ValueError(f"ARCHIVE_PATH_UNREWRITABLE:{source_id}")
            if rewritten != stored_path:
                connection.execute(
                    "UPDATE source_records SET archive_path = ? WHERE id = ?",
                    (rewritten, source_id),
                )
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        connection.close()


def freeze_demo_caches(project_root: Path, fixtures_dir: Path) -> dict[str, str]:
    """冻结三类同材料缓存：风险 Ingest、当前规则 Query、全范围 Lint。

    载荷由真实抽取器从演示材料定位（chunk id/locator 均非手造），写入前逐条
    通过对应工作流输出 schema 校验；每条缓存记录 source SHA-256、baseline
    version、prompt version、model label 与 schema version。
    """
    project_root = project_root.resolve()
    fixtures_dir = fixtures_dir.resolve()
    cache = AiCache(project_root / DATABASE_REL)
    # AiCache 默认相对 CWD 解析缓存目录；快照构建必须显式指向目标 project_root。
    cache.cache_dir = project_root / CACHE_DIR_REL
    frozen: dict[str, str] = {}

    risk_bytes = (fixtures_dir / RISK_OPINION_FILENAME).read_bytes()
    risk_sha256 = hashlib.sha256(risk_bytes).hexdigest()
    risk_source_id = f"SRC-{risk_sha256[:16].upper()}"
    risk_extracted = extract_document_bytes(
        risk_bytes,
        filename="风险意见.md",
        source_id=risk_source_id,
    )
    risk_chunk = next(chunk for chunk in risk_extracted.chunks if RISK_SENTENCE in chunk.text)

    ingest_payload = {
        "schema_version": "1.0",
        "task_id": f"INGEST-{risk_sha256[:16].upper()}",
        "summary": "识别到一条需会议裁决的风险意见。",
        "items": [
            {
                "item_id": "ITEM-RISK-001",
                "item_type": "professional_opinion",
                "title": "客群限制意见",
                "content": RISK_SENTENCE,
                "target_card_id": RULE_CARD_ID,
                "result_type": "conflict_discussion",
                "status": "conflict",
                "source_citations": [
                    {
                        "source_id": risk_source_id,
                        "chunk_id": risk_chunk.chunk_id,
                        "locator": risk_chunk.locator,
                        "excerpt": risk_chunk.text[:40],
                    }
                ],
                "confidence": 0.86,
                "uncertainty": "尚未形成正式决定",
            }
        ],
        "relations": [
            {
                "source_id": "ITEM-RISK-001",
                "relation_type": "conflicts_with",
                "target_id": RULE_CARD_ID,
            }
        ],
    }
    IngestWorkflowOutput.model_validate(ingest_payload)
    ingest_identity = CacheIdentity(
        task_type="ingest",
        source_sha256=risk_sha256,
        baseline_version=BASELINE_VERSION,
        prompt_version="ingest-v1",
        model_label="dify-ingest",
        schema_version="1.0",
    )
    cache.put(ingest_identity, ingest_payload)
    frozen["ingest"] = ingest_identity.cache_key

    base_archive = (
        project_root / "data/source_archive" / PROJECT_ID / BASE_SOURCE_ID / BASE_SOURCE_FILENAME
    )
    base_bytes = base_archive.read_bytes()
    base_sha256 = hashlib.sha256(base_bytes).hexdigest()
    base_extracted = extract_document_bytes(
        base_bytes,
        filename=BASE_SOURCE_FILENAME,
        source_id=BASE_SOURCE_ID,
    )
    base_chunk = next(chunk for chunk in base_extracted.chunks if RULE_CARD_CONTENT in chunk.text)
    query_payload = {
        "answer": RULE_CARD_CONTENT,
        "effective_rules": [RULE_CARD_ID],
        "citations": [
            {
                "id": base_chunk.chunk_id,
                "source_id": BASE_SOURCE_ID,
                "filename": BASE_SOURCE_FILENAME,
                "document_version": "v1.0",
                "section": base_chunk.locator,
                "excerpt": base_chunk.text,
                "authority_level": "formal_effective",
            }
        ],
        "candidate_notice": None,
        "conflict_notice": None,
        "baseline_version": BASELINE_VERSION,
        "evidence_sufficiency": "sufficient",
        "result_mode": "realtime",
        "model_call_id": None,
    }
    QueryWorkflowOutput.model_validate(query_payload)
    query_identity = CacheIdentity(
        task_type="query",
        source_sha256=base_sha256,
        baseline_version=BASELINE_VERSION,
        prompt_version="query-v1",
        model_label="dify-query",
        schema_version="1.0",
        question=DEMO_QUESTION,
    )
    cache.put(query_identity, query_payload)
    frozen["query"] = query_identity.cache_key

    baseline_full = (
        project_root / "data/obsidian_vault/02_Current_Baseline" / BASELINE_VERSION / "full.md"
    )
    baseline_extracted = extract_document_bytes(
        baseline_full.read_bytes(),
        filename="full.md",
        source_id=BASELINE_ID,
    )
    baseline_chunk = next(
        chunk for chunk in baseline_extracted.chunks if RULE_CARD_CONTENT in chunk.text
    )
    lint_payload = {
        "schema_version": "1.0",
        "issues": [
            {
                "issue_type": "conflict",
                "severity": "pending_decision",
                "title": "客群边界不一致",
                "description": "正式风险意见要求收紧目标客群，需要会议确认执行口径。",
                "evidence": [
                    {
                        "source_id": BASELINE_ID,
                        "citation_id": "CIT-BASE-001",
                        "excerpt": RULE_CARD_CONTENT,
                        "document_version": BASELINE_VERSION,
                        "page_or_section": baseline_chunk.locator,
                        "side": "current_baseline",
                    },
                    {
                        "source_id": risk_source_id,
                        "citation_id": risk_chunk.chunk_id,
                        "excerpt": risk_chunk.text[:40],
                        "document_version": "v1.0",
                        "page_or_section": risk_chunk.locator,
                        "side": "challenging_source",
                    },
                ],
                "impacted_domains": ["产品", "风险"],
                "options": [{"code": "A", "label": "收紧", "impact": "调整产品规则"}],
                "ai_recommendation": "A",
                "ai_confidence": 0.78,
                "uncertainty": "专业意见尚未形成正式决定",
            }
        ],
    }
    LintWorkflowOutput.model_validate(lint_payload)
    lint_identity = CacheIdentity(
        task_type="lint",
        source_sha256=risk_sha256,
        baseline_version=BASELINE_VERSION,
        prompt_version="lint-v1",
        model_label="dify-lint",
        schema_version="1.0",
    )
    cache.put(lint_identity, lint_payload)
    frozen["lint"] = lint_identity.cache_key
    return frozen
