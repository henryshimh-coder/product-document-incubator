"""T12 演示环境校验：Manifest、基线资产、SQLite 镜像、缓存与来源归档。

用法（仓库根目录）：

    python scripts/validate_data.py                      # 校验仓库演示环境
    python scripts/validate_data.py --root /tmp/t12_run  # 校验指定环境
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.bootstrap_demo import BASELINE_VERSION  # noqa: E402
from scripts.snapshot_common import validate_data  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the T12 demo environment.")
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help="演示环境 project_root。")
    parser.add_argument(
        "--expect-baseline",
        default=BASELINE_VERSION,
        help=f"期望的基线版本（默认 {BASELINE_VERSION}）。",
    )
    arguments = parser.parse_args(argv)
    report = validate_data(arguments.root.resolve(), arguments.expect_baseline)
    if not report.ok:
        print(f"VALIDATION_FAILED errors={report.errors} checks={report.checks}")
        return 1
    print(f"VALIDATION_OK baseline={report.baseline_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
