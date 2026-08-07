"""T12 reset_demo 集成测试：损坏恢复、归档保护、离线缓存导入与载荷防篡改。

快照源使用仓库已提交的 ``data/demo_snapshots/{initial,frozen}``；被重置的演示环境
一律建在 ``tmp_path`` 下，测试绝不触碰仓库自身的本地状态。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from datetime import date
from pathlib import Path

import httpx
import pytest

from scripts.bootstrap_demo import BASELINE_VERSION, bootstrap
from scripts.demo_materials import DEMO_QUESTION
from scripts.reset_demo import main as reset_main
from scripts.reset_demo import reset_demo
from scripts.snapshot_common import (
    CACHE_DIR_REL,
    DATABASE_REL,
    MANIFEST_REL,
    VAULT_DIR_REL,
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
    with sqlite3.connect(tmp_path / "snap_root_a" / "payload" / DATABASE_REL) as connection:
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


def test_reset_never_touches_source_archive(tmp_path: Path) -> None:
    """正式原始资料（data/source_archive）在重置前后逐字节不变，标记文件保留。"""
    root = _bootstrapped_root(tmp_path)
    base_archive = root / BASE_ARCHIVE_REL
    base_sha_before = hashlib.sha256(base_archive.read_bytes()).hexdigest()
    marker = base_archive.parent.parent / "_demo_marker.txt"
    marker.write_text("正式原始资料标记，重置不得删除。", encoding="utf-8")

    report = reset_demo("initial", root)

    assert report.ok
    assert marker.read_text(encoding="utf-8") == "正式原始资料标记，重置不得删除。"
    assert hashlib.sha256(base_archive.read_bytes()).hexdigest() == base_sha_before


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
        root / "config" / "app.yaml", environ=environ, http_factory=forbidden_factory
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
    base_sha256 = hashlib.sha256((root / BASE_ARCHIVE_REL).read_bytes()).hexdigest()
    expected_source = {"ingest": risk_sha256, "lint": risk_sha256, "query": base_sha256}

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
    ),
)
def test_restore_refuses_tampered_payload(
    tmp_path: Path,
    target_relative: Path,
    fragment: str,
) -> None:
    """快照载荷任一目标被篡改都必须 fail closed，不得进入恢复流程。"""
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


def test_reset_cli_reports_reset_and_validation_ok(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _bootstrapped_root(tmp_path)
    assert reset_main(["--root", str(root), "--snapshot", "frozen"]) == 0
    output = capsys.readouterr().out
    assert "RESET_OK snapshot=frozen" in output
    assert f"VALIDATION_OK baseline={BASELINE_VERSION}" in output
