"""T12 reset/export 集成测试：输出保护、干净恢复、归档种子、事务回滚与兼容闸。

快照源使用仓库已提交的 ``data/demo_snapshots/{initial,frozen}``；被重置的演示环境
一律建在 ``tmp_path`` 下，测试绝不以读写方式打开仓库内快照载荷数据库。
对应评审强制负向用例 T12-R01～R09。
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
from datetime import date
from pathlib import Path

import httpx
import pytest

import scripts.snapshot_common as sc
from scripts.bootstrap_demo import BASELINE_VERSION, bootstrap
from scripts.demo_materials import DEMO_QUESTION
from scripts.reset_demo import main as reset_main
from scripts.reset_demo import reset_demo
from scripts.snapshot_common import (
    CACHE_DIR_REL,
    DATABASE_REL,
    MANIFEST_REL,
    SNAPSHOT_TARGETS,
    VAULT_DIR_REL,
    ValidationReport,
    capture_snapshot,
    restore_snapshot,
    validate_data,
)
from src.application.container import build_container
from src.application.dto.ingest import ImportSourceInput
from src.domain.enums import AuthorityLevel, CallResultMode, SecurityLevel
from src.infrastructure.cache.ai_cache import CURRENT_OUTPUT_SCHEMAS, build_cache_key
from src.infrastructure.db.connection import connect

REPO_ROOT = Path(__file__).resolve().parents[3]
SNAPSHOTS_DIR = REPO_ROOT / "data" / "demo_snapshots"
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "sources"
CONFIG_DIR = REPO_ROOT / "config"
RISK_FIXTURE_SHA256 = "739bf7df1497a8c19f714db5c61840824cc9deff9622fcb5197ebe94e84ac441"
BASE_ARCHIVE_REL = Path("data/source_archive/LLD/SRC-LLD-BASE/当前产品方案.md")
FULL_DOCUMENT_REL = VAULT_DIR_REL / "02_Current_Baseline" / BASELINE_VERSION / "full.md"


def _bootstrapped_root(tmp_path: Path) -> Path:
    root = tmp_path / "demo"
    root.mkdir()
    bootstrap(root)
    return root


def _target_fingerprints(root: Path) -> dict[str, str]:
    """四个显式目标的内容指纹：文件取 SHA-256，目录取确定性摘要。"""
    fingerprints: dict[str, str] = {}
    for relative in SNAPSHOT_TARGETS:
        path = root / relative
        if path.is_dir():
            fingerprints[str(relative)] = sc._directory_sha256(path)
        elif path.is_file():
            fingerprints[str(relative)] = hashlib.sha256(path.read_bytes()).hexdigest()
        else:
            fingerprints[str(relative)] = "<missing>"
    return fingerprints


def _snapshot_dir_digest(snapshot_dir: Path) -> str:
    return sc._directory_sha256(snapshot_dir)


def test_capture_is_byte_deterministic_across_build_roots(tmp_path: Path) -> None:
    """同一演示内容在不同构建根下捕获的快照哈希一致（归档路径已规范化）。"""
    snapshots = []
    for name in ("root_a", "root_b"):
        root = tmp_path / name
        root.mkdir()
        bootstrap(root)
        snapshots.append(capture_snapshot(root, tmp_path / f"snap_{name}"))
    first, second = snapshots
    assert first.database_sha256 == second.database_sha256
    assert first.manifest_sha256 == second.manifest_sha256
    assert first.vault_sha256 == second.vault_sha256
    assert first.cache_index_sha256 == second.cache_index_sha256
    assert first.source_archive_index_sha256 == second.source_archive_index_sha256
    uri = f"file:{tmp_path / 'snap_root_a' / 'payload' / DATABASE_REL}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        rows = connection.execute("SELECT archive_path FROM source_records").fetchall()
    assert rows
    assert all(row[0].startswith("data/source_archive/") for row in rows)


def test_reset_restores_baseline_environment_after_corruption(tmp_path: Path) -> None:
    """计划 Step-1 形状：破坏显式目标后一键重置，环境恢复到可校验基线。"""
    root = _bootstrapped_root(tmp_path)
    (root / MANIFEST_REL).write_text("garbage", encoding="utf-8")
    (root / FULL_DOCUMENT_REL).unlink()
    (root / DATABASE_REL).unlink()
    broken = validate_data(root)
    assert not broken.ok
    assert broken.errors[0].startswith("MANIFEST_INVALID")

    report = reset_demo("initial", root)

    assert report.ok
    assert report.baseline_version == BASELINE_VERSION
    assert report.errors == []
    assert (root / DATABASE_REL).is_file()
    assert (root / FULL_DOCUMENT_REL).is_file()


def test_capture_refuses_output_equal_to_project_root(tmp_path: Path) -> None:
    """T12-R01：snapshot_dir == project_root 在任何写入前拒绝，项目根逐字节不变。"""
    root = _bootstrapped_root(tmp_path)
    before = sc._directory_sha256(root)
    with pytest.raises(ValueError, match="SNAPSHOT_OUTPUT_OVERLAP"):
        capture_snapshot(root, root)
    assert sc._directory_sha256(root) == before
    assert (root / DATABASE_REL).is_file()


def test_capture_refuses_output_overlapping_protected_paths(tmp_path: Path) -> None:
    """T12-R02：输出为项目根祖先、四目标内部或 source_archive 内部全部拒绝。"""
    root = _bootstrapped_root(tmp_path)
    before = sc._directory_sha256(root)
    dangerous_outputs = [
        tmp_path,  # 项目根祖先
        root / "data" / "local_state",  # 覆盖数据库与 Manifest 所在目录
        root / "data" / "local_state" / "cache" / "inner",  # 缓存目录内部
        root / "data" / "obsidian_vault" / "inner",  # Vault 内部
        root / "data" / "source_archive" / "inner",  # 正式来源归档内部
    ]
    for output in dangerous_outputs:
        with pytest.raises(ValueError, match="SNAPSHOT_OUTPUT_OVERLAP"):
            capture_snapshot(root, output)
    assert sc._directory_sha256(root) == before


def test_capture_failure_preserves_previous_snapshot(tmp_path: Path) -> None:
    """T12-R03：构建任一阶段失败，旧快照逐字节保留且不留半成品正式目录。"""
    root = _bootstrapped_root(tmp_path)
    snapshot_dir = tmp_path / "snap"
    capture_snapshot(root, snapshot_dir)
    old_digest = _snapshot_dir_digest(snapshot_dir)

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("INJECTED_COPY_FAILURE")

    monkeypatch = pytest.MonkeyPatch()
    for target in ("shutil.copy2",):
        monkeypatch.setattr(target, boom)
        with pytest.raises(OSError, match="INJECTED_COPY_FAILURE"):
            capture_snapshot(root, snapshot_dir)
        assert _snapshot_dir_digest(snapshot_dir) == old_digest
        assert not list(tmp_path.glob("*.staging-*"))
        monkeypatch.undo()

    monkeypatch.setattr(sc, "_normalize_archive_paths", boom)
    with pytest.raises(OSError, match="INJECTED_COPY_FAILURE"):
        capture_snapshot(root, snapshot_dir)
    assert _snapshot_dir_digest(snapshot_dir) == old_digest
    assert not list(tmp_path.glob("*.staging-*"))
    monkeypatch.undo()

    monkeypatch.setattr(sc, "verify_snapshot_payload", boom)
    with pytest.raises(OSError, match="INJECTED_COPY_FAILURE"):
        capture_snapshot(root, snapshot_dir)
    assert _snapshot_dir_digest(snapshot_dir) == old_digest
    assert not list(tmp_path.glob("*.staging-*"))
    monkeypatch.undo()


def test_restore_initial_into_empty_clean_root(tmp_path: Path) -> None:
    """T12-R04：initial 快照独立恢复到空目录，来源种子补齐，无需 bootstrap。"""
    root = tmp_path / "clean"
    root.mkdir()
    report = reset_demo("initial", root)
    assert report.ok
    assert report.baseline_version == BASELINE_VERSION
    archive = root / BASE_ARCHIVE_REL
    assert archive.is_file()
    fixture = (FIXTURES_DIR / "current_product.md").read_bytes()
    assert archive.read_bytes() == fixture
    with connect(root / DATABASE_REL) as connection:
        row = connection.execute(
            "SELECT archive_path FROM source_records WHERE id = 'SRC-LLD-BASE'"
        ).fetchone()
    assert row["archive_path"] == str(archive)


def test_restore_frozen_into_empty_clean_root(tmp_path: Path) -> None:
    """T12-R05：frozen 快照独立恢复到空目录，三类缓存与来源种子齐全。"""
    root = tmp_path / "clean"
    root.mkdir()
    report = reset_demo("frozen", root)
    assert report.ok
    assert (root / BASE_ARCHIVE_REL).is_file()
    with connect(root / DATABASE_REL) as connection:
        rows = connection.execute("SELECT task_type FROM cache_entries").fetchall()
    assert {row["task_type"] for row in rows} == {"ingest", "query", "lint"}
    cache_files = list((root / CACHE_DIR_REL).glob("*.json"))
    assert len(cache_files) == 3


def test_reset_preserves_extra_source_files(tmp_path: Path) -> None:
    """T12-R06：目标 root 的额外正式来源与标记文件在重置后逐字节保留。"""
    root = _bootstrapped_root(tmp_path)
    base_archive = root / BASE_ARCHIVE_REL
    base_sha_before = hashlib.sha256(base_archive.read_bytes()).hexdigest()
    marker = base_archive.parent.parent / "_demo_marker.txt"
    marker.write_text("正式原始资料标记，重置不得删除。", encoding="utf-8")
    extra = base_archive.parent.parent / "SRC-EXTRA" / "额外材料.md"
    extra.parent.mkdir()
    extra.write_text("额外正式来源内容。", encoding="utf-8")

    report = reset_demo("initial", root)

    assert report.ok
    assert marker.read_text(encoding="utf-8") == "正式原始资料标记，重置不得删除。"
    assert extra.read_text(encoding="utf-8") == "额外正式来源内容。"
    assert hashlib.sha256(base_archive.read_bytes()).hexdigest() == base_sha_before


def test_restore_refuses_conflicting_source_archive(tmp_path: Path) -> None:
    """T12-R07：同路径来源与快照哈希不一致时，覆盖任何目标前 fail closed。"""
    root = _bootstrapped_root(tmp_path)
    before = _target_fingerprints(root)
    base_archive = root / BASE_ARCHIVE_REL
    base_archive.write_text("被本地改写的正式来源，与快照不一致。", encoding="utf-8")

    with pytest.raises(ValueError, match="ARCHIVE_CONFLICT:SRC-LLD-BASE"):
        restore_snapshot(SNAPSHOTS_DIR / "initial", root)

    assert _target_fingerprints(root) == before
    assert base_archive.read_text(encoding="utf-8") == "被本地改写的正式来源，与快照不一致。"


def test_restore_rolls_back_when_path_rewrite_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T12-R08：数据库路径改写失败，四个目标与来源保持恢复前状态。"""
    root = _bootstrapped_root(tmp_path)
    before = _target_fingerprints(root)

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("INJECTED_REWRITE_FAILURE")

    monkeypatch.setattr(sc, "_rewrite_archive_paths", boom)
    with pytest.raises(OSError, match="INJECTED_REWRITE_FAILURE"):
        restore_snapshot(SNAPSHOTS_DIR / "initial", root)

    assert _target_fingerprints(root) == before
    assert validate_data(root).ok
    assert not list(root.glob(".reset-*"))


def test_restore_rolls_back_when_target_replace_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T12-R08：第 2 个目标替换失败，已替换目标回滚、已备份目标还原。"""
    root = _bootstrapped_root(tmp_path)
    before = _target_fingerprints(root)
    real_replace = os.replace

    def flaky_replace(src: object, dst: object) -> None:
        if ".reset-staging" in str(src) and str(dst).endswith("current_baseline.json"):
            raise OSError("INJECTED_REPLACE_FAILURE")
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", flaky_replace)
    with pytest.raises(OSError, match="INJECTED_REPLACE_FAILURE"):
        restore_snapshot(SNAPSHOTS_DIR / "initial", root)

    assert _target_fingerprints(root) == before
    assert validate_data(root).ok
    assert not list(root.glob(".reset-*"))


def test_restore_rolls_back_when_final_validation_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T12-R08：最终 validate 失败，四个目标完整回滚，旧环境仍可校验。"""
    root = _bootstrapped_root(tmp_path)
    before = _target_fingerprints(root)
    monkeypatch.setattr(
        sc,
        "validate_data",
        lambda *args, **kwargs: ValidationReport(
            ok=False,
            baseline_version=BASELINE_VERSION,
            errors=["INJECTED_VALIDATION_FAILURE"],
        ),
    )
    report = restore_snapshot(SNAPSHOTS_DIR / "initial", root)

    assert not report.ok
    assert report.errors == ["INJECTED_VALIDATION_FAILURE"]
    assert _target_fingerprints(root) == before
    assert validate_data(root).ok
    assert not list(root.glob(".reset-*"))


def test_restore_refuses_forged_or_unknown_versions(tmp_path: Path) -> None:
    """T12-R09：伪造 app/schema 版本或清单带额外字段，恢复前拒绝且零变更。"""
    root = _bootstrapped_root(tmp_path)
    before = _target_fingerprints(root)
    manifest_path = SNAPSHOTS_DIR / "initial" / "manifest.json"
    document = json.loads(manifest_path.read_text(encoding="utf-8"))

    cases = [
        ({"app_version": "999.0.0"}, "SNAPSHOT_APP_VERSION_MISMATCH"),
        ({"schema_version": "999.0"}, "SNAPSHOT_SCHEMA_VERSION_UNSUPPORTED"),
        ({"unexpected_field": "x"}, "SNAPSHOT_MANIFEST_INVALID"),
    ]
    for patch, code in cases:
        snapshot_copy = tmp_path / f"snap_{code}"
        shutil.copytree(SNAPSHOTS_DIR / "initial", snapshot_copy)
        forged = {**document, **patch}
        (snapshot_copy / "manifest.json").write_text(
            json.dumps(forged, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match=code):
            restore_snapshot(snapshot_copy, root)
        assert _target_fingerprints(root) == before


def test_restore_refuses_when_lock_held(tmp_path: Path) -> None:
    """重置锁被占用时直接失败，不进入任何替换阶段。"""
    root = _bootstrapped_root(tmp_path)
    before = _target_fingerprints(root)
    lock_path = root / "data" / "local_state" / ".reset.lock"
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        with pytest.raises(ValueError, match="RESET_LOCKED"):
            restore_snapshot(SNAPSHOTS_DIR / "initial", root)
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)
    assert _target_fingerprints(root) == before


def test_offline_ingest_from_frozen_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """恢复 frozen 快照后断网导入风险材料：命中冻结缓存并产出冲突议题。"""
    root = _bootstrapped_root(tmp_path)
    reset_demo("frozen", root)
    shutil.copytree(CONFIG_DIR, root / "config")
    monkeypatch.chdir(root)

    def forbidden_factory() -> httpx.Client:
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError(f"NETWORK_FORBIDDEN:{request.url}")

        return httpx.Client(transport=httpx.MockTransport(handler))

    environ = {
        "DIFY_BASE_URL": "https://dify.offline.local",
        "DIFY_INGEST_API_KEY": "ingest-key",
        "DIFY_QUERY_API_KEY": "query-key",
        "DIFY_LINT_API_KEY": "lint-key",
    }
    container = build_container(
        root / "config" / "app.yaml",
        environ=environ,
        http_factory=forbidden_factory,
    )
    assert container.import_source is not None

    report = container.import_source.execute(
        ImportSourceInput(
            project_id="LLD",
            uploaded_name="风险意见.md",
            uploaded_bytes=(FIXTURES_DIR / "risk_opinion.md").read_bytes(),
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
            preferred_mode="cache",
        )
    )

    assert report.result_mode == CallResultMode.CACHE
    assert report.model_call_id is None
    assert report.cache_generated_at is not None
    assert report.conflict_count == 1
    assert len(report.created_card_ids) == 1
    assert len(report.created_issue_ids) == 1


def test_frozen_snapshot_cache_entries_carry_full_metadata(tmp_path: Path) -> None:
    """每条冻结缓存记录 source SHA-256、baseline、prompt、model 与 schema 版本。"""
    root = _bootstrapped_root(tmp_path)
    reset_demo("frozen", root)
    with connect(root / DATABASE_REL) as connection:
        rows = connection.execute(
            """
            SELECT cache_key, task_type, source_sha256, baseline_version,
                   prompt_version, model_label, schema_version,
                   response_json, response_sha256
            FROM cache_entries ORDER BY task_type
            """
        ).fetchall()
    assert {row["task_type"] for row in rows} == {"ingest", "query", "lint"}

    risk_sha256 = hashlib.sha256((FIXTURES_DIR / "risk_opinion.md").read_bytes()).hexdigest()
    assert risk_sha256 == RISK_FIXTURE_SHA256
    # query 身份绑定当前基线全文材料内容哈希（真实运行时输入），lint/ingest 绑定风险材料。
    baseline_full_sha256 = hashlib.sha256((root / FULL_DOCUMENT_REL).read_bytes()).hexdigest()
    expected_source = {"ingest": risk_sha256, "lint": risk_sha256, "query": baseline_full_sha256}

    for row in rows:
        task_type = row["task_type"]
        assert row["source_sha256"] == expected_source[task_type]
        assert row["baseline_version"] == BASELINE_VERSION
        assert row["prompt_version"] == f"{task_type}-v1"
        assert row["model_label"] == f"dify-{task_type}"
        assert row["schema_version"] == "1.0"
        payload_bytes = (root / CACHE_DIR_REL / f"{row['cache_key']}.json").read_bytes()
        assert hashlib.sha256(payload_bytes).hexdigest() == row["response_sha256"]
        response_text = payload_bytes.decode("utf-8")
        assert response_text == row["response_json"]
        value = json.loads(response_text)
        canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        assert canonical == response_text
        CURRENT_OUTPUT_SCHEMAS[task_type].model_validate(value)
        question = DEMO_QUESTION if task_type == "query" else ""
        rebuilt = build_cache_key(
            task_type,
            row["source_sha256"],
            row["baseline_version"],
            row["prompt_version"],
            row["model_label"],
            row["schema_version"],
            question,
        )
        assert rebuilt == row["cache_key"]
        if task_type == "ingest":
            assert value["task_id"] == f"INGEST-{risk_sha256[:16].upper()}"
        elif task_type == "lint":
            assert value["issues"][0]["issue_type"] == "conflict"


@pytest.mark.parametrize(
    ("target_relative", "fragment"),
    (
        (FULL_DOCUMENT_REL, "vault"),
        (DATABASE_REL, "database"),
        (MANIFEST_REL, "manifest"),
        (BASE_ARCHIVE_REL, "source_archive_index"),
    ),
)
def test_restore_refuses_tampered_payload(
    tmp_path: Path,
    target_relative: Path,
    fragment: str,
) -> None:
    """快照载荷任一目标（含来源种子）被篡改都必须 fail closed。"""
    snapshot_copy = tmp_path / "snapshot"
    shutil.copytree(SNAPSHOTS_DIR / "initial", snapshot_copy)
    with (snapshot_copy / "payload" / target_relative).open("ab") as file:
        file.write(b"tamper")
    root = tmp_path / "demo"
    root.mkdir()

    with pytest.raises(ValueError, match=f"SNAPSHOT_PAYLOAD_MISMATCH:{fragment}"):
        restore_snapshot(snapshot_copy, root)


def test_restore_refuses_missing_project_root(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="RESET_ROOT_MISSING"):
        restore_snapshot(SNAPSHOTS_DIR / "initial", tmp_path / "missing")


def test_reset_demo_unknown_snapshot_raises(tmp_path: Path) -> None:
    root = _bootstrapped_root(tmp_path)
    with pytest.raises(ValueError, match="SNAPSHOT_NOT_FOUND"):
        reset_demo("nonexistent-snapshot", root)


def test_repo_snapshot_payloads_have_no_residue() -> None:
    """仓库内快照载荷不得出现 WAL 侧车、staging、backup 或临时文件。"""
    forbidden = []
    for path in SNAPSHOTS_DIR.rglob("*"):
        name = path.name
        if (
            name.endswith(("-wal", "-shm", ".tmp"))
            or name.startswith((".reset-", ".freeze-scratch-"))
            or ".staging-" in name
            or ".old-" in name
        ):
            forbidden.append(str(path))
    assert forbidden == []


def test_reset_cli_reports_reset_and_validation_ok(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _bootstrapped_root(tmp_path)
    assert reset_main(["--root", str(root), "--snapshot", "frozen"]) == 0
    output = capsys.readouterr().out
    assert "RESET_OK snapshot=frozen" in output
    assert f"VALIDATION_OK baseline={BASELINE_VERSION}" in output
