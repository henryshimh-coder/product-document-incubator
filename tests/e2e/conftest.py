"""T13 E2E fixtures：每个测试从快照创建独立临时数据目录（计划 Step 1）。

- `demo_root` / `container` / `harness`：从 initial 快照恢复（空缓存），实时 mock 网关。
- `frozen_root` / `frozen_container` / `frozen_harness`：从 frozen 快照恢复（含三类
  冻结缓存），供超时回退等缓存用例；网关可按任务注入超时。
测试后临时目录随 tmp_path 丢弃，仓库 data/ 零写入。
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from scripts.snapshot_common import DATABASE_REL, MANIFEST_REL, restore_snapshot
from src.application.container import AppContainer, build_container
from src.infrastructure.files.manifest_store import ManifestStore
from tests.e2e.harness import MOCK_ENVIRON, DemoHarness, mock_http_factory

REPO_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOTS_DIR = REPO_ROOT / "data" / "demo_snapshots"
CONFIG_DIR = REPO_ROOT / "config"


def _restored_root(tmp_path: Path, snapshot: str) -> Path:
    root = tmp_path / "demo"
    root.mkdir()
    report = restore_snapshot(SNAPSHOTS_DIR / snapshot, root)
    assert report.ok, f"snapshot {snapshot} restore failed: {report.errors}"
    shutil.copytree(CONFIG_DIR, root / "config", dirs_exist_ok=True)
    return root


def _build(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    timeout_tasks: frozenset[str],
    http_factory=None,
    record: list[dict] | None = None,
) -> AppContainer:
    # 归档与缓存根目录按 CWD 解析，固定到临时工程根。
    monkeypatch.chdir(root)
    return build_container(
        root / "config" / "app.yaml",
        environ={**MOCK_ENVIRON, "INCUBATOR_LIBRARY_ROOT": str(root)},
        http_factory=http_factory or (lambda: mock_http_factory(timeout_tasks, record=record)),
    )


@pytest.fixture
def gateway_calls() -> list[dict]:
    """按时间顺序收集本轮测试的全部出站网关调用（顺序敏感断言的见证）。"""
    return []


@pytest.fixture
def make_container(monkeypatch: pytest.MonkeyPatch):
    """按需构建容器（超时注入/禁网/录制），统一在 teardown 释放状态锁。"""
    containers: list[AppContainer] = []

    def factory(
        root: Path,
        *,
        timeout_tasks: frozenset[str] = frozenset(),
        http_factory=None,
        environ: dict[str, str] | None = None,
    ) -> AppContainer:
        monkeypatch.chdir(root)
        container = build_container(
            root / "config" / "app.yaml",
            environ={
                **(environ or MOCK_ENVIRON),
                "INCUBATOR_LIBRARY_ROOT": str(root),
            },
            http_factory=http_factory or (lambda: mock_http_factory(timeout_tasks)),
        )
        containers.append(container)
        return container

    yield factory

    for container in containers:
        container.close()


@pytest.fixture
def demo_root(tmp_path: Path) -> Path:
    return _restored_root(tmp_path, "initial")


@pytest.fixture
def container(
    demo_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    gateway_calls: list[dict],
) -> Iterator[AppContainer]:
    container = _build(demo_root, monkeypatch, frozenset(), record=gateway_calls)
    try:
        yield container
    finally:
        container.close()


@pytest.fixture
def harness(container: AppContainer) -> DemoHarness:
    return DemoHarness(container)


@pytest.fixture
def frozen_root(tmp_path: Path) -> Path:
    return _restored_root(tmp_path, "frozen")


@pytest.fixture
def frozen_container(
    frozen_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[AppContainer]:
    container = _build(frozen_root, monkeypatch, frozenset())
    try:
        yield container
    finally:
        container.close()


@pytest.fixture
def frozen_harness(frozen_container: AppContainer) -> DemoHarness:
    return DemoHarness(frozen_container)


@pytest.fixture
def manifest_store(demo_root: Path) -> ManifestStore:
    return ManifestStore(demo_root / MANIFEST_REL, project_root=demo_root)


@pytest.fixture
def db_path(demo_root: Path) -> Path:
    return demo_root / DATABASE_REL
