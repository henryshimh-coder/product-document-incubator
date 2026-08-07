"""T12 一键重置：把演示环境安全恢复到指定快照。

只覆盖四个显式目标（数据库、Manifest、缓存目录、Vault），绝不删除
`data/source_archive/` 中的正式原始资料；恢复后自动运行 validate_data。

用法（仓库根目录）：

    python scripts/reset_demo.py                     # 恢复 initial 快照
    python scripts/reset_demo.py --snapshot frozen   # 恢复 frozen 快照
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.snapshot_common import ValidationReport, restore_snapshot  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]


def reset_demo(snapshot: Path | str, project_root: Path) -> ValidationReport:
    """恢复指定快照并自动校验；snapshot 可为快照名或快照目录。"""
    snapshot_dir = _resolve_snapshot_dir(snapshot)
    return restore_snapshot(snapshot_dir, project_root)


def _resolve_snapshot_dir(snapshot: Path | str) -> Path:
    candidate = Path(snapshot)
    if candidate.is_dir():
        return candidate.resolve()
    named = REPO_ROOT / "data/demo_snapshots" / str(snapshot)
    if named.is_dir():
        return named.resolve()
    raise ValueError(f"SNAPSHOT_NOT_FOUND:{snapshot}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reset the demo environment to a snapshot.")
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help="演示环境 project_root。")
    parser.add_argument(
        "--snapshot",
        default="initial",
        help="快照名或快照目录（默认 initial）。",
    )
    arguments = parser.parse_args(argv)
    report = reset_demo(arguments.snapshot, arguments.root.resolve())
    if not report.ok:
        print(f"RESET_FAILED errors={report.errors}")
        return 1
    print(f"RESET_OK snapshot={arguments.snapshot}")
    print(f"VALIDATION_OK baseline={report.baseline_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
