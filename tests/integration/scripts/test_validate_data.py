"""T12 validate_data 集成测试：有效环境与七类损坏的 fail-closed 证据。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from scripts.bootstrap_demo import (
    BASE_SOURCE_CONTENT,
    BASELINE_VERSION,
    bootstrap,
)
from scripts.demo_materials import (
    CURRENT_PRODUCT_FILENAME,
    MATERIAL_BUILDERS,
    RISK_OPINION_FILENAME,
)
from scripts.reset_demo import reset_demo
from scripts.snapshot_common import (
    CACHE_DIR_REL,
    DATABASE_REL,
    MANIFEST_REL,
    VAULT_DIR_REL,
    validate_data,
)
from scripts.validate_data import main as validate_main

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "sources"
RISK_FIXTURE_SHA256 = "739bf7df1497a8c19f714db5c61840824cc9deff9622fcb5197ebe94e84ac441"
BASE_ARCHIVE_REL = Path("data/source_archive/LLD/SRC-LLD-BASE/当前产品方案.md")
FULL_DOCUMENT_REL = VAULT_DIR_REL / "02_Current_Baseline" / BASELINE_VERSION / "full.md"
EXPECTED_CHECKS = [
    "manifest_parse",
    "baseline_version",
    "baseline_assets",
    "sqlite_mirror",
    "cache_integrity",
    "source_archives",
]


def _bootstrapped_root(tmp_path: Path) -> Path:
    root = tmp_path / "demo"
    root.mkdir()
    bootstrap(root)
    return root


def test_validate_ok_on_fresh_bootstrap(tmp_path: Path) -> None:
    report = validate_data(_bootstrapped_root(tmp_path))
    assert report.ok
    assert report.baseline_version == BASELINE_VERSION
    assert report.checks == EXPECTED_CHECKS
    assert report.errors == []


def test_manifest_garbage_fails_closed(tmp_path: Path) -> None:
    root = _bootstrapped_root(tmp_path)
    (root / MANIFEST_REL).write_text("garbage", encoding="utf-8")
    report = validate_data(root)
    assert not report.ok
    assert report.errors[0].startswith("MANIFEST_INVALID")


def test_baseline_version_mismatch_fails_closed(tmp_path: Path) -> None:
    root = _bootstrapped_root(tmp_path)
    manifest_path = root / MANIFEST_REL
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["current_version"] = "LLD-999_9"
    manifest_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    report = validate_data(root)
    assert not report.ok
    assert report.errors == ["BASELINE_VERSION_MISMATCH:LLD-999_9"]
    assert report.checks == ["manifest_parse"]


def test_baseline_asset_mismatch_fails_closed(tmp_path: Path) -> None:
    root = _bootstrapped_root(tmp_path)
    with (root / FULL_DOCUMENT_REL).open("ab") as file:
        file.write("篡改。".encode())
    report = validate_data(root)
    assert not report.ok
    assert report.errors[0].startswith("BASELINE_ASSET_MISMATCH:")


def test_sqlite_mirror_mismatch_fails_closed(tmp_path: Path) -> None:
    root = _bootstrapped_root(tmp_path)
    with sqlite3.connect(root / DATABASE_REL) as connection:
        connection.execute("UPDATE projects SET current_baseline_id = 'BASE-FAKE' WHERE id = 'LLD'")
    report = validate_data(root)
    assert not report.ok
    assert report.errors == ["SQLITE_MIRROR_MISMATCH"]


def test_cache_file_mismatch_fails_closed(tmp_path: Path) -> None:
    root = _bootstrapped_root(tmp_path)
    reset_demo("frozen", root)
    cache_file = next((root / CACHE_DIR_REL).glob("*.json"))
    with cache_file.open("ab") as file:
        file.write(b"x")
    report = validate_data(root)
    assert not report.ok
    assert report.errors[0].startswith("CACHE_FILE_MISMATCH:")


def test_archive_mismatch_fails_closed(tmp_path: Path) -> None:
    root = _bootstrapped_root(tmp_path)
    with (root / BASE_ARCHIVE_REL).open("ab") as file:
        file.write("篡改。".encode())
    report = validate_data(root)
    assert not report.ok
    assert report.errors == ["ARCHIVE_MISMATCH:SRC-LLD-BASE"]


def test_archive_path_escape_fails_closed(tmp_path: Path) -> None:
    root = _bootstrapped_root(tmp_path)
    with sqlite3.connect(root / DATABASE_REL) as connection:
        connection.execute(
            "UPDATE source_records SET archive_path = '/etc/hosts' WHERE id = 'SRC-LLD-BASE'"
        )
    report = validate_data(root)
    assert not report.ok
    assert report.errors == ["ARCHIVE_PATH_ESCAPE:SRC-LLD-BASE"]


def test_fixtures_match_single_source_builders() -> None:
    """四份夹具必须与唯一生成规则逐字节一致，基线/风险材料身份可被外部验收引用。"""
    for filename, builder in MATERIAL_BUILDERS.items():
        fixture_bytes = (FIXTURES_DIR / filename).read_bytes()
        assert fixture_bytes == builder().encode("utf-8"), filename
    current = (FIXTURES_DIR / CURRENT_PRODUCT_FILENAME).read_bytes()
    assert current == BASE_SOURCE_CONTENT.encode("utf-8")
    risk_sha256 = hashlib.sha256((FIXTURES_DIR / RISK_OPINION_FILENAME).read_bytes()).hexdigest()
    assert risk_sha256 == RISK_FIXTURE_SHA256


def test_validate_cli_reports_ok(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _bootstrapped_root(tmp_path)
    assert validate_main(["--root", str(root)]) == 0
    output = capsys.readouterr().out
    assert f"VALIDATION_OK baseline={BASELINE_VERSION}" in output


def test_validate_cli_reports_failure(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _bootstrapped_root(tmp_path)
    (root / MANIFEST_REL).write_text("garbage", encoding="utf-8")
    assert validate_main(["--root", str(root)]) == 1
    output = capsys.readouterr().out
    assert "VALIDATION_FAILED" in output
