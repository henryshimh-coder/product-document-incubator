from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from tests.e2e.test_incubator_full_success import IncubatorHarness


def _validator_module():
    path = Path(__file__).resolve().parents[3] / "scripts/validate_incubator.py"
    spec = importlib.util.spec_from_file_location("validate_incubator", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_validator_reports_project_and_source_counts(tmp_path: Path) -> None:
    harness = IncubatorHarness(tmp_path)
    paths = harness.create_project("PROJECT_A", "产品 A")
    source = harness.archive(paths, "需求.md", "产品需求".encode())

    report = _validator_module().validate_incubator(harness.library_root)

    assert report.projects == 1
    assert report.current_projects == 0
    assert report.sources == 1
    assert source.archive_path.is_file()
