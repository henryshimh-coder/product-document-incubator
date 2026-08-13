from __future__ import annotations

from src.application.container import build_container
from tests.e2e.test_incubator_full_success import IncubatorHarness


def test_active_project_is_restored_after_container_restart(tmp_path) -> None:
    harness = IncubatorHarness(tmp_path)
    paths = harness.create_project("PROJECT_A", "产品 A")
    harness.manager.switch(paths.project_id)

    container = build_container(environ={"INCUBATOR_LIBRARY_ROOT": str(harness.library_root)})

    assert container.active_project is not None
    assert container.active_project.project_id == "PROJECT_A"
    assert container.archive_raw_source is not None
    assert container.export_current_document is not None
    container.close()
