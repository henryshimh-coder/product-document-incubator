"""T13 安全测试 fixtures：独立临时工程根 + 可录制出站载荷的容器构建器。"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from scripts.snapshot_common import restore_snapshot
from src.application.container import AppContainer, build_container
from tests.e2e.harness import MOCK_ENVIRON, mock_http_factory

REPO_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOTS_DIR = REPO_ROOT / "data" / "demo_snapshots"
CONFIG_DIR = REPO_ROOT / "config"


@pytest.fixture
def demo_root(tmp_path: Path) -> Path:
    root = tmp_path / "demo"
    root.mkdir()
    report = restore_snapshot(SNAPSHOTS_DIR / "initial", root)
    assert report.ok, f"initial restore failed: {report.errors}"
    shutil.copytree(CONFIG_DIR, root / "config", dirs_exist_ok=True)
    return root


@pytest.fixture
def make_container(demo_root: Path, monkeypatch: pytest.MonkeyPatch):
    """构建带出站录制的容器；environ 可注入脱敏词典。teardown 释放状态锁。"""
    containers: list[AppContainer] = []

    def factory(
        *,
        environ: dict[str, str] | None = None,
        record: list[dict] | None = None,
    ) -> AppContainer:
        monkeypatch.chdir(demo_root)
        container = build_container(
            demo_root / "config" / "app.yaml",
            environ=environ or MOCK_ENVIRON,
            http_factory=lambda: mock_http_factory(record=record),
        )
        containers.append(container)
        return container

    yield factory

    for container in containers:
        container.close()
