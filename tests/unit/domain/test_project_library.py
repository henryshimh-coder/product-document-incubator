from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError


def test_incubator_settings_and_project_summary_reject_blank_or_negative_values() -> None:
    """Catches an unusable Owner setup and impossible project-card counters."""
    from src.domain.incubator import IncubatorSettings, ProjectSummary

    with pytest.raises(ValidationError):
        IncubatorSettings(owner_name=" ", library_root="/tmp/library")
    with pytest.raises(ValidationError):
        ProjectSummary(
            project_id="PROJECT_A",
            name="项目 A",
            stage="待初始化",
            current_version=None,
            source_count=-1,
            draft_count=0,
            updated_at=datetime(2026, 8, 12, tzinfo=UTC),
        )


def test_project_paths_stay_inside_library_root(tmp_path: Path) -> None:
    """Catches project paths drifting outside the selected local library."""
    from src.infrastructure.files.project_library import ProjectPaths

    paths = ProjectPaths.for_project(tmp_path / "library", "CREDIT-CARD-01")

    assert paths.project_root == (tmp_path / "library/CREDIT-CARD-01").resolve()
    assert paths.raw_root == paths.project_root / "raw"
    assert paths.wiki_root == paths.project_root / "wiki"
    assert paths.schema_root == paths.project_root / "schema"
    assert paths.exports_root == paths.project_root / "exports"
    assert paths.system_root == paths.project_root / ".incubator"
    assert paths.manifest_path == paths.system_root / "current-baseline.json"


def test_paths_accept_registered_root_outside_control_root(tmp_path: Path) -> None:
    """Catches central control roots being mistaken for every project's content root."""
    from src.infrastructure.files.project_library import ProjectPaths

    control = tmp_path / "control"
    project_root = tmp_path / "external/PROJECT_A"

    paths = ProjectPaths.for_registered_root(control, "PROJECT_A", project_root)

    assert paths.library_root == control.resolve()
    assert paths.project_root == project_root.resolve()
    assert paths.raw_root == project_root.resolve() / "raw"


def test_registered_root_rejects_symlink_and_derived_escape(tmp_path: Path) -> None:
    """Catches a registered root or child path escaping the project through a symlink."""
    from src.infrastructure.files.project_library import ProjectPaths

    target = tmp_path / "real/PROJECT_A"
    target.mkdir(parents=True)
    link = tmp_path / "linked-project"
    link.symlink_to(target, target_is_directory=True)

    with pytest.raises(ValueError, match="project root must not be a symlink"):
        ProjectPaths.for_registered_root(tmp_path / "control", "PROJECT_A", link)

    outside = tmp_path / "outside"
    outside.mkdir()
    (target / "raw").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="derived project path"):
        ProjectPaths.for_registered_root(tmp_path / "control", "PROJECT_A", target)


@pytest.mark.parametrize("project_id", ["../LLD", "a/b", "lld", "", "A B"])
def test_project_paths_reject_unsafe_project_id(tmp_path: Path, project_id: str) -> None:
    """Catches traversal, separators, lowercase IDs, blanks, and spaces."""
    from src.infrastructure.files.project_library import ProjectPaths

    with pytest.raises(ValueError, match="project_id"):
        ProjectPaths.for_project(tmp_path / "library", project_id)


def test_project_paths_reject_existing_symlink_escape(tmp_path: Path) -> None:
    """Catches a valid-looking ID resolving through a symlink outside the library."""
    from src.infrastructure.files.project_library import ProjectPaths

    library_root = tmp_path / "library"
    library_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (library_root / "ESCAPE").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink|outside library_root"):
        ProjectPaths.for_project(library_root, "ESCAPE")


def test_project_paths_reject_symlink_to_another_project_in_same_library(tmp_path: Path) -> None:
    """Catches one project ID resolving to another project's files inside the library."""
    from src.infrastructure.files.project_library import ProjectPaths

    library_root = tmp_path / "library"
    project_b = library_root / "PROJECT_B"
    project_b.mkdir(parents=True)
    (library_root / "PROJECT_A").symlink_to(project_b, target_is_directory=True)

    with pytest.raises(ValueError, match="project root must not be a symlink"):
        ProjectPaths.for_project(library_root, "PROJECT_A")


def test_project_paths_reject_manifest_symlink_escape(tmp_path: Path) -> None:
    """Catches the baseline pointer resolving outside an otherwise safe project."""
    from src.infrastructure.files.project_library import ProjectPaths

    library_root = tmp_path / "library"
    system_root = library_root / "SAFE/.incubator"
    system_root.mkdir(parents=True)
    outside_manifest = tmp_path / "outside-manifest.json"
    outside_manifest.write_text("{}", encoding="utf-8")
    (system_root / "current-baseline.json").symlink_to(outside_manifest)

    with pytest.raises(ValueError, match="outside library_root"):
        ProjectPaths.for_project(library_root, "SAFE")


def test_library_locator_prefers_environment_over_pointer_and_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches a stale pointer overriding an explicit Owner/runtime selection."""
    from src.infrastructure.files.project_library import ProjectLibraryLocator

    pointer = tmp_path / "data/local_state/incubator-root.json"
    pointer.parent.mkdir(parents=True)
    pointer.write_text(
        json.dumps({"library_root": str(tmp_path / "from-pointer")}), encoding="utf-8"
    )
    monkeypatch.setenv("INCUBATOR_LIBRARY_ROOT", str(tmp_path / "from-env"))

    locator = ProjectLibraryLocator(pointer_path=pointer, home_directory=tmp_path / "home")

    assert locator.resolve() == (tmp_path / "from-env").resolve()


def test_library_locator_uses_pointer_then_owner_home_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches loss of the persisted Owner library choice and an incorrect default."""
    from src.infrastructure.files.project_library import ProjectLibraryLocator

    monkeypatch.delenv("INCUBATOR_LIBRARY_ROOT", raising=False)
    pointer = tmp_path / "data/local_state/incubator-root.json"
    locator = ProjectLibraryLocator(pointer_path=pointer, home_directory=tmp_path / "home")

    assert locator.resolve() == (tmp_path / "home/Documents/产品文档孵化器项目库").resolve()

    locator.save_pointer(tmp_path / "owner-library")

    assert locator.resolve() == (tmp_path / "owner-library").resolve()
    assert json.loads(pointer.read_text(encoding="utf-8")) == {
        "library_root": str((tmp_path / "owner-library").resolve())
    }
    assert not list(pointer.parent.glob(".incubator-root.json.tmp-*"))


def test_settings_store_rejects_system_directory_symlink_escape(tmp_path: Path) -> None:
    """Catches Owner settings being written outside the selected project library."""
    from src.domain.incubator import IncubatorSettings
    from src.infrastructure.files.project_library import JsonIncubatorSettingsStore

    library_root = tmp_path / "library"
    library_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (library_root / ".incubator").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="outside library_root"):
        JsonIncubatorSettingsStore(library_root).save(
            IncubatorSettings(
                owner_name="产品经理",
                library_root=str(library_root),
                current_project_id=None,
            )
        )

    assert not (outside / "settings.json").exists()


def test_settings_store_rejects_payload_for_a_different_library_root(tmp_path: Path) -> None:
    """Catches UI paths and filesystem operations silently targeting different libraries."""
    from src.infrastructure.files.project_library import JsonIncubatorSettingsStore

    library_root = tmp_path / "library-a"
    settings_path = library_root / ".incubator/settings.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(
        json.dumps(
            {
                "owner_name": "产品经理",
                "library_root": str((tmp_path / "library-b").resolve()),
                "current_project_id": None,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="does not match settings store"):
        JsonIncubatorSettingsStore(library_root).load()
