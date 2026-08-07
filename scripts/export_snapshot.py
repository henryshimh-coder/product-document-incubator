"""T12 导出演示快照：把演示环境的四个显式目标捕获为可校验快照。

用法（仓库根目录）：

    python scripts/export_snapshot.py --root /tmp/t12_build --name initial
    python scripts/export_snapshot.py --root /tmp/t12_build --name frozen --freeze-demo-caches
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.snapshot_common import (  # noqa: E402
    SnapshotManifest,
    capture_snapshot,
    freeze_demo_caches,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def export_snapshot(
    project_root: Path,
    snapshot_dir: Path,
    *,
    freeze_caches: bool = False,
    fixtures_dir: Path | None = None,
) -> SnapshotManifest:
    """先按需冻结缓存，再把当前状态捕获为快照。"""
    if freeze_caches:
        frozen = freeze_demo_caches(
            project_root,
            fixtures_dir or (REPO_ROOT / "tests/fixtures/sources"),
        )
        for task_type, cache_key in sorted(frozen.items()):
            print(f"FREEZE_OK {task_type} cache_key={cache_key}")
    return capture_snapshot(project_root, snapshot_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export a verifiable T12 demo snapshot.")
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help="演示环境 project_root。")
    parser.add_argument("--name", default="initial", help="快照名（默认 initial）。")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="快照输出目录（默认 data/demo_snapshots/<name>）。",
    )
    parser.add_argument(
        "--freeze-demo-caches",
        action="store_true",
        help="捕获前冻结三类同材料缓存（ingest/query/lint）。",
    )
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=None,
        help="演示材料目录（默认 tests/fixtures/sources）。",
    )
    arguments = parser.parse_args(argv)
    output = arguments.output or (REPO_ROOT / "data/demo_snapshots" / arguments.name)
    snapshot = export_snapshot(
        arguments.root.resolve(),
        output,
        freeze_caches=arguments.freeze_demo_caches,
        fixtures_dir=arguments.fixtures.resolve() if arguments.fixtures else None,
    )
    print(
        f"EXPORT_OK name={arguments.name} baseline={snapshot.baseline_version} "
        f"database_sha256={snapshot.database_sha256}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
