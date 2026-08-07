"""T12 演示快照共享原语：SnapshotManifest、捕获、恢复、校验与冻结缓存。

快照内容（相对 project_root 的四个显式目标，恢复时只允许覆盖它们）：

- ``data/local_state/product_intelligence.db``
- ``data/local_state/current_baseline.json``
- ``data/local_state/cache/``
- ``data/obsidian_vault/``

安全边界（评审整改 M1/M2/M4 后生效）：

- 捕获在任何写入前拒绝与 project_root、四个目标或 ``data/source_archive/``
  重叠的输出目录（含符号链接解析后的重叠），错误码
  ``SNAPSHOT_OUTPUT_OVERLAP`` / ``SNAPSHOT_OUTPUT_UNSAFE``。
- 捕获在同文件系统 staging 目录中完成复制、归档路径规范化、来源种子收集与
  载荷复验，再原子替换目标目录；构建失败时旧快照逐字节保留。
- 快照额外携带数据库所引用来源归档的最小种子（``payload/data/source_archive/``），
  干净空目录无需 bootstrap 即可独立恢复；恢复不删除额外来源文件，同路径同哈希
  直接复用，同路径不同哈希在覆盖任何目标前 fail closed（``ARCHIVE_CONFLICT``）。
- 恢复在 project 级文件锁内进行，先 staging 准备 + 兼容闸 + 归档预检，再备份
  原目标并逐个替换；任一替换、路径改写或最终 ``validate_data`` 失败都完整回滚。
- ``SnapshotManifest`` 严格模型（extra=forbid、冻结、SHA-256 字段、UTC 时间），
  ``app_version``/``schema_version`` 在恢复前参与兼容性拒绝。
- 快照载荷数据库一律只读方式打开（mode=ro），不产生 ``-wal/-shm`` 侧车。
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tomllib
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import httpx
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    StringConstraints,
    ValidationError,
    field_validator,
)

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.bootstrap_demo import (  # noqa: E402
    BASELINE_ID,
    BASELINE_VERSION,
    PROJECT_ID,
    RULE_CARD_ID,
)
from scripts.demo_materials import (  # noqa: E402
    DEMO_QUESTION,
    RISK_OPINION_FILENAME,
    RISK_SENTENCE,
)
from src.application.container import build_container  # noqa: E402
from src.application.dto.ingest import ImportSourceInput  # noqa: E402
from src.application.dto.lint import RunLintInput  # noqa: E402
from src.application.dto.query import RunQueryInput  # noqa: E402
from src.domain.enums import AuthorityLevel, SecurityLevel  # noqa: E402
from src.infrastructure.cache.ai_cache import (  # noqa: E402
    CURRENT_OUTPUT_SCHEMAS,
    AiCache,
    CacheIdentity,
    build_cache_key,
)
from src.infrastructure.db.connection import connect  # noqa: E402
from src.infrastructure.db.state_lock import STATE_LOCK_REL  # noqa: E402
from src.infrastructure.files.manifest_store import ManifestStore  # noqa: E402

DATABASE_REL = Path("data/local_state/product_intelligence.db")
MANIFEST_REL = Path("data/local_state/current_baseline.json")
CACHE_DIR_REL = Path("data/local_state/cache")
VAULT_DIR_REL = Path("data/obsidian_vault")
SNAPSHOT_TARGETS: tuple[Path, ...] = (DATABASE_REL, MANIFEST_REL, CACHE_DIR_REL, VAULT_DIR_REL)
SOURCE_ARCHIVE_REL = Path("data/source_archive")
# 与应用侧共享同一路径：应用持共享锁，重置持排他锁（评审第二轮 Important）。
RESET_LOCK_REL = STATE_LOCK_REL

SNAPSHOT_SCHEMA_VERSION = "1.1"
PAYLOAD_DIRNAME = "payload"
MANIFEST_FILENAME = "manifest.json"

FREEZE_ENVIRON = {
    "DIFY_BASE_URL": "https://dify.freeze.local",
    "DIFY_INGEST_API_KEY": "ingest-key",
    "DIFY_QUERY_API_KEY": "query-key",
    "DIFY_LINT_API_KEY": "lint-key",
}

Sha256 = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]


class SnapshotManifest(BaseModel):
    """快照清单：严格模型，记录四个目标与来源种子的内容哈希。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    app_version: str
    schema_version: str
    baseline_version: str
    database_sha256: Sha256
    manifest_sha256: Sha256
    vault_sha256: Sha256
    cache_index_sha256: Sha256
    source_archive_index_sha256: Sha256
    created_at: AwareDatetime

    @field_validator("created_at")
    @classmethod
    def _created_at_must_be_utc(cls, value: datetime) -> datetime:
        if value.utcoffset() != timedelta(0):
            raise ValueError("created_at must be UTC")
        return value


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
    """应用版本取自 pyproject.toml；读取失败必须 fail closed，不回退字面量。"""
    root = repo_root or Path(__file__).resolve().parents[1]
    try:
        document = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        return str(document["project"]["version"])
    except (OSError, KeyError, tomllib.TOMLDecodeError) as error:
        raise ValueError(f"APP_VERSION_UNAVAILABLE:{type(error).__name__}") from error


@contextmanager
def _closed_connection(db_path: Path) -> Iterator[sqlite3.Connection]:
    """显式关闭的只读查询连接，避免未关闭连接把 WAL 侧车留在目录里。"""
    connection = connect(db_path)
    try:
        yield connection
    finally:
        connection.close()


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
    # 必须显式关闭连接：`with connect(...)` 只管事务不关闭连接，未关闭的
    # 连接会让 WAL 侧车残留在目标目录。
    connection = connect(db_path)
    try:
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
    finally:
        connection.close()
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
    with _closed_connection(db_path) as connection:
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
    with _closed_connection(db_path) as connection:
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


def _checked_output_dir(project_root: Path, snapshot_dir: Path) -> Path:
    """在任何写入前拒绝危险的快照输出目录（评审 T12-P0-01 及第二轮 Critical）。

    除根/根祖先/受保护目标重叠外，已存在且含内容的目录必须是快照形态
    （manifest.json + payload/）才允许被替换——普通目录（如 src/、docs/）
    里的哨兵文件绝不被快照替换吞掉。
    """
    try:
        resolved = snapshot_dir.resolve()
    except (OSError, RuntimeError) as error:
        raise ValueError(f"SNAPSHOT_OUTPUT_UNSAFE:{snapshot_dir}") from error
    if resolved == project_root or project_root.is_relative_to(resolved):
        raise ValueError(f"SNAPSHOT_OUTPUT_OVERLAP:{resolved}")
    for relative in (*SNAPSHOT_TARGETS, SOURCE_ARCHIVE_REL):
        protected = (project_root / relative).resolve()
        if resolved.is_relative_to(protected) or protected.is_relative_to(resolved):
            raise ValueError(f"SNAPSHOT_OUTPUT_OVERLAP:{resolved}")
    if resolved.exists():
        if not resolved.is_dir():
            raise ValueError(f"SNAPSHOT_OUTPUT_UNSAFE:{resolved}")
        has_content = any(resolved.iterdir())
        looks_like_snapshot = (resolved / MANIFEST_FILENAME).is_file() and (
            resolved / PAYLOAD_DIRNAME
        ).is_dir()
        if has_content and not looks_like_snapshot:
            raise ValueError(f"SNAPSHOT_OUTPUT_NONSNAPSHOT:{resolved}")
    return resolved


def capture_snapshot(
    project_root: Path,
    snapshot_dir: Path,
    *,
    expected_baseline: str = BASELINE_VERSION,
) -> SnapshotManifest:
    """把演示环境捕获为可校验快照（staging 构建 + 原子替换）。

    捕获前先运行 `validate_data`：损坏的环境不允许生成正式快照。全部内容在
    目标旁的 staging 目录中构建并复验通过后才替换正式快照目录；构建失败时
    旧快照逐字节保留，staging 可安全清理。
    """
    project_root = project_root.resolve()
    snapshot_dir = _checked_output_dir(project_root, snapshot_dir)
    report = validate_data(project_root, expected_baseline)
    if not report.ok:
        raise ValueError(f"REFUSE_TO_SNAPSHOT_INVALID_STATE:{report.errors}")
    # WAL 模式下近期提交可能只存在于 -wal 侧车文件；复制主库文件前必须落盘。
    with connect(project_root / DATABASE_REL) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    staging = snapshot_dir.parent / f".{snapshot_dir.name}.staging-{uuid4().hex[:8]}"
    try:
        payload_root = staging / PAYLOAD_DIRNAME
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
        _capture_source_seeds(project_root, payload_root)
        snapshot = SnapshotManifest(
            app_version=app_version(),
            schema_version=SNAPSHOT_SCHEMA_VERSION,
            baseline_version=expected_baseline,
            database_sha256=_sha256(payload_root / DATABASE_REL),
            manifest_sha256=_sha256(payload_root / MANIFEST_REL),
            vault_sha256=_directory_sha256(payload_root / VAULT_DIR_REL),
            cache_index_sha256=_directory_sha256(payload_root / CACHE_DIR_REL),
            source_archive_index_sha256=_directory_sha256(payload_root / SOURCE_ARCHIVE_REL),
            created_at=datetime.now(UTC),
        )
        (staging / MANIFEST_FILENAME).write_text(
            json.dumps(snapshot.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        # 替换正式目录前复验 staging 载荷，清单描述的就是入库字节。
        verify_snapshot_payload(staging)
        _replace_tree(staging, snapshot_dir)
    except BaseException:
        # 清理失败不得掩盖原始错误。
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return snapshot


def _capture_source_seeds(project_root: Path, payload_root: Path) -> None:
    """把数据库引用的来源归档复制为快照种子，逐个核对内容哈希。"""
    for record in _read_source_records(payload_root / DATABASE_REL, read_only=True):
        relative = Path(record["archive_path"])
        live = project_root / relative
        if not live.is_file() or _sha256(live) != record["sha256"]:
            raise ValueError(f"SNAPSHOT_SEED_SOURCE_MISMATCH:{record['id']}")
        seed = payload_root / relative
        seed.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(live, seed)


def _replace_tree(staging: Path, target: Path) -> None:
    """用 staging 原子替换 target 目录；失败时把旧目录换回原位。"""
    backup = target.parent / f".{target.name}.old-{uuid4().hex[:8]}"
    parked = False
    try:
        if target.exists():
            os.replace(target, backup)
            parked = True
        os.replace(staging, target)
    except BaseException:
        if parked and not target.exists():
            os.replace(backup, target)
        raise
    if parked:
        # 备份清理失败只留残渣，不影响新快照一致性。
        shutil.rmtree(backup, ignore_errors=True)


def verify_snapshot_payload(snapshot_dir: Path) -> SnapshotManifest:
    """读取快照清单并核对载荷哈希；任何不符都 fail closed。"""
    snapshot_dir = snapshot_dir.resolve()
    manifest_path = snapshot_dir / MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise ValueError(f"SNAPSHOT_MANIFEST_MISSING:{snapshot_dir}")
    try:
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"SNAPSHOT_MANIFEST_INVALID:{type(error).__name__}") from error
    try:
        snapshot = SnapshotManifest.model_validate(document)
    except ValidationError as error:
        raise ValueError(f"SNAPSHOT_MANIFEST_INVALID:{type(error).__name__}") from error
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
    seed_hash = _directory_sha256(payload_root / SOURCE_ARCHIVE_REL)
    if seed_hash != snapshot.source_archive_index_sha256:
        mismatches.append("source_archive_index")
    if mismatches:
        raise ValueError(f"SNAPSHOT_PAYLOAD_MISMATCH:{','.join(mismatches)}")
    return snapshot


def _require_compatible(snapshot: SnapshotManifest) -> None:
    """恢复前的版本兼容闸：未知 schema 或异版本应用直接拒绝。"""
    if snapshot.schema_version != SNAPSHOT_SCHEMA_VERSION:
        raise ValueError(f"SNAPSHOT_SCHEMA_VERSION_UNSUPPORTED:{snapshot.schema_version}")
    current = app_version()
    if snapshot.app_version != current:
        raise ValueError(f"SNAPSHOT_APP_VERSION_MISMATCH:{snapshot.app_version}")


@contextmanager
def _reset_lock(project_root: Path) -> Iterator[None]:
    """project 级重置锁：应用或另一重置进程持锁时直接失败。"""
    lock_path = project_root / RESET_LOCK_REL
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        os.close(descriptor)
        raise ValueError(f"RESET_LOCKED:{project_root}") from error
    try:
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def restore_snapshot(snapshot_dir: Path, project_root: Path) -> ValidationReport:
    """受控恢复：staging 准备、兼容闸、备份替换、失败回滚。

    只覆盖四个显式目标；`data/source_archive/` 中已有内容绝不删除，缺失的
    数据库引用来源从快照种子补齐，同路径不同哈希在覆盖任何目标前 fail
    closed。恢复后自动运行 `validate_data`；任一阶段失败都会把四个目标
    回滚到恢复前状态。
    """
    project_root = project_root.resolve()
    if not project_root.is_dir():
        raise ValueError(f"RESET_ROOT_MISSING:{project_root}")
    snapshot = verify_snapshot_payload(snapshot_dir)
    _require_compatible(snapshot)
    payload_root = snapshot_dir.resolve() / PAYLOAD_DIRNAME
    with _reset_lock(project_root):
        seed_plan = _plan_source_seeds(payload_root, project_root)
        staging = project_root / f".reset-staging-{uuid4().hex[:8]}"
        backup = project_root / f".reset-backup-{uuid4().hex[:8]}"
        parked: list[Path] = []
        placed: list[Path] = []
        seeded: list[Path] = []
        try:
            for relative in SNAPSHOT_TARGETS:
                source = payload_root / relative
                target = staging / relative
                if source.is_dir():
                    shutil.copytree(source, target)
                elif source.is_file():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
                else:
                    target.mkdir(parents=True, exist_ok=True)
            # 数据库路径改写与 WAL 落盘都在 staging 内完成。
            _rewrite_archive_paths(staging / DATABASE_REL, project_root)
            for sidecar_suffix in ("-wal", "-shm"):
                (staging / Path(f"{DATABASE_REL}{sidecar_suffix}")).unlink(missing_ok=True)
            try:
                for relative in seed_plan:
                    destination = project_root / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    # 先登记回滚：部分写入失败时 _rollback 能清掉残留；
                    # 临时文件 + rename 保证目标路径只出现完整内容。
                    seeded.append(destination)
                    temporary = destination.parent / (
                        f".{destination.name}.seed-{uuid4().hex[:8]}.tmp"
                    )
                    try:
                        shutil.copy2(payload_root / relative, temporary)
                        os.replace(temporary, destination)
                    finally:
                        temporary.unlink(missing_ok=True)
                for relative in SNAPSHOT_TARGETS:
                    parked.extend(_park_existing(project_root, backup, relative))
                    _place_staged(staging, project_root, relative)
                    placed.append(relative)
            except BaseException as error:
                _rollback(project_root, backup, parked, placed, seeded, error)
                raise
            report = validate_data(project_root, snapshot.baseline_version)
            if not report.ok:
                error = ValueError(f"RESET_VALIDATION_FAILED:{report.errors}")
                _rollback(project_root, backup, parked, placed, seeded, error)
                return report
        finally:
            # 清理失败不得掩盖原始结果。
            shutil.rmtree(staging, ignore_errors=True)
            shutil.rmtree(backup, ignore_errors=True)
        return report


def _park_existing(project_root: Path, backup: Path, relative: Path) -> list[Path]:
    """把现存目标（含数据库 WAL 侧车）改名进备份目录，返回已备份的相对路径。"""
    parked: list[Path] = []
    candidates = [relative]
    if relative == DATABASE_REL:
        candidates.extend(Path(f"{relative}{suffix}") for suffix in ("-wal", "-shm"))
    for candidate in candidates:
        origin = project_root / candidate
        if not (origin.exists() or origin.is_symlink()):
            continue
        destination = backup / candidate
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(origin, destination)
        parked.append(candidate)
    return parked


def _place_staged(staging: Path, project_root: Path, relative: Path) -> None:
    source = staging / relative
    destination = project_root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source, destination)


def _rollback(
    project_root: Path,
    backup: Path,
    parked: list[Path],
    placed: list[Path],
    seeded: list[Path],
    original_error: BaseException,
) -> None:
    """回滚四个显式目标与本次补种的来源文件；问题记入原始异常不掩盖它。"""
    problems: list[str] = []
    for relative in reversed(placed):
        target = project_root / relative
        try:
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            else:
                target.unlink(missing_ok=True)
        except OSError as error:
            problems.append(f"remove {relative}: {error}")
    for relative in reversed(parked):
        try:
            os.replace(backup / relative, project_root / relative)
        except OSError as error:
            problems.append(f"restore {relative}: {error}")
    for path in seeded:
        try:
            path.unlink(missing_ok=True)
        except OSError as error:
            problems.append(f"unseed {path}: {error}")
    if problems:
        original_error.add_note("rollback problems: " + "; ".join(problems))


def _plan_source_seeds(payload_root: Path, project_root: Path) -> list[Path]:
    """计算需要补种的来源相对路径；归档冲突在覆盖任何目标前 fail closed。"""
    plan: list[Path] = []
    for record in _read_source_records(payload_root / DATABASE_REL, read_only=True):
        relative = Path(record["archive_path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"SNAPSHOT_ARCHIVE_PATH_INVALID:{record['id']}")
        seed = payload_root / relative
        if not seed.is_file() or _sha256(seed) != record["sha256"]:
            raise ValueError(f"SNAPSHOT_SEED_MISMATCH:{record['id']}")
        target = project_root / relative
        if target.exists():
            if not target.is_file() or _sha256(target) != record["sha256"]:
                raise ValueError(f"ARCHIVE_CONFLICT:{record['id']}")
            # 同路径同哈希直接复用，不重复写入。
        else:
            plan.append(relative)
    return plan


def _read_source_records(db_path: Path, *, read_only: bool) -> list[sqlite3.Row]:
    """读取快照数据库的来源记录；载荷数据库一律只读打开，不留侧车文件。

    载荷库在捕获时已 VACUUM + checkpoint(TRUNCATE)，内容完全落在主库文件；
    以 ``immutable=1`` 打开可跳过 WAL 索引恢复，只读连接不会在载荷目录里
    重建 ``-shm/-wal`` 侧车。
    """
    if read_only:
        connection = sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)
    else:
        connection = sqlite3.connect(db_path)
    try:
        connection.row_factory = sqlite3.Row
        return connection.execute(
            "SELECT id, archive_path, sha256, size_bytes FROM source_records ORDER BY id"
        ).fetchall()
    finally:
        connection.close()


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


def freeze_demo_caches(
    project_root: Path,
    fixtures_dir: Path,
    *,
    config_dir: Path | None = None,
) -> dict[str, str]:
    """冻结三类同材料缓存：风险 Ingest、当前规则 Query、全范围 Lint。

    在 project_root 的一次性克隆（scratch）中以真实 Use Case + 确定性模拟
    网关跑实时流程，缓存载荷因此完全由运行时代码产出并通过与实时一致的
    校验；随后把三条缓存工件收割进正式环境。scratch 整体丢弃，正式环境的
    领域状态（卡片/Issue/Relation/发布）不因冻结而改变。
    """
    project_root = project_root.resolve()
    fixtures_dir = fixtures_dir.resolve()
    config_root = (config_dir or (Path(__file__).resolve().parents[1] / "config")).resolve()
    scratch = project_root.parent / f".freeze-scratch-{uuid4().hex[:8]}"
    caller_cwd = Path.cwd()
    try:
        shutil.copytree(project_root / "data", scratch / "data")
        shutil.copytree(config_root, scratch / "config")
        # scratch 数据库继承正式环境的绝对归档路径；先改写到 scratch 自身根。
        _rewrite_archive_paths(scratch / DATABASE_REL, scratch)
        # 归档根与缓存目录按进程工作目录解析，冻结全程以 scratch 为 CWD。
        os.chdir(scratch)
        container = build_container(
            scratch / "config" / "app.yaml",
            environ=FREEZE_ENVIRON,
            http_factory=_freeze_http_factory,
        )
        if container.import_source is None or container.query is None or container.lint is None:
            raise ValueError("FREEZE_CONTAINER_INCOMPLETE")
        risk_bytes = (fixtures_dir / RISK_OPINION_FILENAME).read_bytes()
        risk_report = container.import_source.execute(
            ImportSourceInput(
                project_id=PROJECT_ID,
                uploaded_name="风险意见.md",
                uploaded_bytes=risk_bytes,
                source_type="risk_opinion",
                authority_level=AuthorityLevel.FORMAL_DECISION,
                source_department="风险",
                provider=None,
                document_date=date(2026, 8, 4),
                document_version="v1.0",
                applicable_baseline_version=BASELINE_VERSION,
                security_level=SecurityLevel.L2_INTERNAL,
                is_redacted_confirmed=True,
                allow_external_model=True,
                is_sandbox=False,
                preferred_mode="realtime",
            )
        )
        container.query.execute(
            RunQueryInput(
                project_id=PROJECT_ID,
                question=DEMO_QUESTION,
                scope="effective",
                preferred_mode="realtime",
            )
        )
        container.lint.execute(
            RunLintInput(
                project_id=PROJECT_ID,
                scope="current_plus_source",
                source_id=risk_report.source_id,
                preferred_mode="realtime",
            )
        )
        harvested = _read_cache_entries(scratch / DATABASE_REL)
        if len(harvested) != 3 or {row["task_type"] for row in harvested} != {
            "ingest",
            "query",
            "lint",
        }:
            raise ValueError(
                "FREEZE_HARVEST_INCOMPLETE:"
                + ",".join(sorted(row["task_type"] for row in harvested))
            )
        live_cache = AiCache(project_root / DATABASE_REL)
        # AiCache 默认相对 CWD 解析缓存目录；快照构建必须显式指向目标 project_root。
        live_cache.cache_dir = project_root / CACHE_DIR_REL
        frozen: dict[str, str] = {}
        for row in harvested:
            question = DEMO_QUESTION if row["task_type"] == "query" else ""
            identity = CacheIdentity(
                task_type=row["task_type"],
                source_sha256=row["source_sha256"],
                baseline_version=row["baseline_version"],
                prompt_version=row["prompt_version"],
                model_label=row["model_label"],
                schema_version=row["schema_version"],
                question=question,
            )
            if identity.cache_key != row["cache_key"]:
                raise ValueError(f"FREEZE_IDENTITY_MISMATCH:{row['task_type']}")
            live_cache.put(identity, json.loads(row["response_json"]))
            frozen[row["task_type"]] = row["cache_key"]
        return frozen
    finally:
        os.chdir(caller_cwd)
        shutil.rmtree(scratch, ignore_errors=True)


def _read_cache_entries(db_path: Path) -> list[sqlite3.Row]:
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        connection.row_factory = sqlite3.Row
        return connection.execute(
            """
            SELECT cache_key, task_type, source_sha256, baseline_version,
                   prompt_version, model_label, schema_version, response_json
            FROM cache_entries ORDER BY task_type
            """
        ).fetchall()
    finally:
        connection.close()


def _freeze_http_factory() -> httpx.Client:
    """冻结用确定性模拟网关：应答全部来自请求输入中的真实卡片/片段。"""
    return httpx.Client(transport=httpx.MockTransport(_freeze_handler))


def _freeze_handler(request: httpx.Request) -> httpx.Response:
    auth = request.headers.get("authorization", "")
    inputs = json.loads(request.content.decode("utf-8"))["inputs"]
    if "ingest" in auth:
        result = _freeze_ingest_result(inputs)
    elif "query" in auth:
        result = _freeze_query_result(inputs)
    elif "lint" in auth:
        result = _freeze_lint_result(inputs)
    else:  # pragma: no cover - 防御未知任务
        return httpx.Response(400, json={"message": "unknown task key"})
    return httpx.Response(
        200,
        json={"workflow_run_id": "WF-FREEZE-001", "data": {"outputs": {"result": result}}},
    )


def _freeze_ingest_result(inputs: dict) -> dict:
    if inputs["source"]["type"] != "risk_opinion":
        return {
            "schema_version": "1.0",
            "task_id": inputs["task_id"],
            "summary": "留档材料，无需提取候选知识。",
            "items": [],
            "relations": [],
        }
    chunk = next(
        (c for c in inputs["source_chunks"] if RISK_SENTENCE in c["text"]),
        inputs["source_chunks"][0],
    )
    return {
        "schema_version": "1.0",
        "task_id": inputs["task_id"],
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
                        "source_id": inputs["source"]["id"],
                        "chunk_id": chunk["chunk_id"],
                        "locator": chunk["locator"],
                        "excerpt": chunk["text"][:40],
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


def _freeze_query_result(inputs: dict) -> dict:
    card = next(c for c in inputs["effective_cards"] if c["id"] == RULE_CARD_ID)
    citation = next(
        (
            c
            for c in inputs.get("citations", [])
            if c["id"] in set(card.get("source_citations", []))
        ),
        (inputs.get("citations") or [None])[0],
    )
    return {
        "answer": card["content"],
        "effective_rules": [card["id"]],
        "citations": [citation] if citation else [],
        "candidate_notice": None,
        "conflict_notice": None,
        "baseline_version": inputs["baseline_version"],
        "evidence_sufficiency": "sufficient",
        "result_mode": "realtime",
        "model_call_id": None,
    }


def _freeze_lint_result(inputs: dict) -> dict:
    base = next(r for r in inputs["baseline_rules"] if r["id"] == RULE_CARD_ID)
    compare = inputs["comparison_items"][0]
    return {
        "schema_version": "1.0",
        "issues": [
            {
                "issue_type": "conflict",
                "severity": "pending_decision",
                "title": "客群边界不一致",
                "description": "正式风险意见要求收紧目标客群，需要会议确认执行口径。",
                "evidence": [
                    {
                        "source_id": base["source_id"],
                        "citation_id": base["citation_id"],
                        "excerpt": base["excerpt"],
                        "document_version": base["document_version"],
                        "page_or_section": base["page_or_section"],
                        "side": "current_baseline",
                    },
                    {
                        "source_id": compare["source_id"],
                        "citation_id": compare["citation_id"],
                        "excerpt": compare["excerpt"],
                        "document_version": compare["document_version"],
                        "page_or_section": compare["page_or_section"],
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
