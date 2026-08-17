from __future__ import annotations

import json
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.domain.incubator import IncubatorSettings
from src.infrastructure.db.migrations import migrate
from src.infrastructure.db.repositories import SqliteProjectRepository
from src.infrastructure.files.project_library import JsonIncubatorSettingsStore

NOW = datetime(2026, 8, 12, 8, 0, tzinfo=UTC)


@pytest.fixture
def schema_assets() -> Path:
    return Path("assets/incubator_schema")


@pytest.fixture
def now() -> datetime:
    return NOW


@pytest.fixture
def project_environment(tmp_path: Path):
    from src.application.use_cases.manage_projects import ManageProjects
    from src.infrastructure.files.project_scaffolder import ProjectScaffolder

    library_root = tmp_path / "产品文档孵化器项目库"
    database_path = library_root / ".incubator/product_incubator.db"
    migrate(database_path)
    settings = JsonIncubatorSettingsStore(library_root)
    settings.save(
        IncubatorSettings(
            owner_name="产品经理",
            library_root=str(library_root.resolve()),
            current_project_id=None,
        )
    )
    projects = SqliteProjectRepository(database_path)
    scaffolder = ProjectScaffolder(
        library_root=library_root,
        schema_source=Path("assets/incubator_schema"),
        now=lambda: NOW,
    )
    manager = ManageProjects(
        library_root=library_root,
        projects=projects,
        scaffolder=scaffolder,
        settings=settings,
        now=lambda: NOW,
    )
    return manager, library_root, projects, settings


def new_project_command(project_id: str):
    from src.application.dto.projects import CreateProjectInput

    return CreateProjectInput(
        project_id=project_id,
        name=f"{project_id} 产品",
        description="验证产品方案孵化",
        initial_display_version=None,
        allow_external_model=False,
    )


def test_scaffolder_builds_complete_2_2_wiki_llm_tree(
    tmp_path: Path, schema_assets: Path, now: datetime
) -> None:
    """Catches a scaffold missing the 2.2 Wiki-LLM entry points or work areas."""
    from src.infrastructure.files.project_scaffolder import ProjectScaffolder

    parent = tmp_path / "projects"
    parent.mkdir()
    scaffolder = ProjectScaffolder(
        library_root=tmp_path / "control", schema_source=schema_assets, now=lambda: now
    )

    prepared = scaffolder.prepare(new_project_command("PROJECT_A"), parent_root=parent)

    required = {
        "README.md",
        "AGENTS.md",
        "wiki/sources",
        "wiki/drafts/local-ingest",
        "schema/ingest-contract.md",
        "schema/source-page-template.md",
        "schema/topic-page-template.md",
        ".incubator/transactions",
        ".incubator/locks",
    }
    assert all((prepared.temp_root / item).exists() for item in required)
    assert (
        json.loads((prepared.temp_root / ".incubator/project.json").read_text(encoding="utf-8"))
        ["schema_version"]
        == "2.2"
    )
    scaffolder.abort(prepared)


def test_create_project_scaffolds_complete_wiki_atomically(project_environment) -> None:
    """Catches a registered project missing required local Wiki-LLM structure."""
    manager, library_root, _, _ = project_environment

    project = manager.create(new_project_command("NEW_PRODUCT"))

    root = library_root / "NEW_PRODUCT"
    assert project.stage == "待初始化"
    assert (root / "raw").is_dir()
    assert (root / "wiki/current").is_dir()
    assert (root / "wiki/drafts").is_dir()
    assert (root / "wiki/versions").is_dir()
    assert (root / "wiki/topics").is_dir()
    assert (root / "schema/AGENTS.md").is_file()
    assert (root / "schema/product-document-template.md").is_file()
    assert (root / "schema/field-conventions.md").is_file()
    assert (root / "exports").is_dir()
    assert (root / ".incubator/project.json").is_file()
    assert (root / ".incubator/source-index.json").is_file()
    assert not list(library_root.glob(".NEW_PRODUCT.tmp-*"))
    assert (
        json.loads((root / ".incubator/project.json").read_text(encoding="utf-8"))["project_id"]
        == "NEW_PRODUCT"
    )


def test_create_project_rejects_duplicate_id_without_touching_existing_files(
    project_environment,
) -> None:
    """Catches a repeated ID overwriting a project's existing local content."""
    manager, library_root, _, _ = project_environment
    manager.create(new_project_command("DUPLICATE"))
    marker = library_root / "DUPLICATE/raw/owner-note.txt"
    marker.write_text("keep", encoding="utf-8")

    with pytest.raises(ValueError, match="already exists"):
        manager.create(new_project_command("DUPLICATE"))

    assert marker.read_text(encoding="utf-8") == "keep"


def test_create_project_failure_leaves_no_registered_or_visible_project(
    project_environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches a filesystem failure leaking a half-created project."""
    manager, library_root, projects, _ = project_environment

    def fail_commit(*_args, **_kwargs):
        raise OSError("disk")

    monkeypatch.setattr(manager.scaffolder, "commit", fail_commit)

    with pytest.raises(OSError, match="disk"):
        manager.create(new_project_command("BROKEN"))

    assert not (library_root / "BROKEN").exists()
    assert not list(library_root.glob(".BROKEN.tmp-*"))
    assert all(item.id != "BROKEN" for item in projects.list_all())


def test_database_failure_quarantines_committed_directory(
    project_environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches DB failure deleting files or exposing an unregistered project."""
    manager, library_root, projects, _ = project_environment

    def fail_add(_project):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(projects, "add", fail_add)

    with pytest.raises(RuntimeError, match="database unavailable"):
        manager.create(new_project_command("QUARANTINE"))

    assert not (library_root / "QUARANTINE").exists()
    quarantined = list((library_root / ".incubator/quarantine").glob("QUARANTINE-*"))
    assert len(quarantined) == 1
    assert (quarantined[0] / "schema/AGENTS.md").is_file()


def test_create_project_holds_library_lock_until_database_registration(
    project_environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches concurrent creators both passing the directory-existence check."""
    manager, _, projects, _ = project_environment
    entered_add = threading.Event()
    release_add = threading.Event()
    original_add = projects.add

    def delayed_add(project):
        entered_add.set()
        assert release_add.wait(timeout=2)
        original_add(project)

    monkeypatch.setattr(projects, "add", delayed_add)
    first_errors: list[BaseException] = []
    second_errors: list[BaseException] = []

    def create_first():
        try:
            manager.create(new_project_command("CONCURRENT"))
        except BaseException as error:  # pragma: no cover - asserted below
            first_errors.append(error)

    def create_second():
        try:
            manager.create(new_project_command("CONCURRENT"))
        except BaseException as error:
            second_errors.append(error)

    first = threading.Thread(target=create_first)
    second = threading.Thread(target=create_second)
    first.start()
    assert entered_add.wait(timeout=2)
    second.start()
    time.sleep(0.05)
    assert second.is_alive()
    release_add.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert first_errors == []
    assert len(second_errors) == 1
    assert isinstance(second_errors[0], ValueError)
    assert "already exists" in str(second_errors[0])
    assert [project.id for project in projects.list_all()] == ["CONCURRENT"]


def test_scaffolder_commit_never_replaces_directory_created_during_race(
    project_environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches a non-cooperating process creating the final directory after preflight."""
    from src.infrastructure.files import project_scaffolder as scaffolder_module

    manager, library_root, _, _ = project_environment
    prepared = manager.scaffolder.prepare(new_project_command("RACE"))
    manager.scaffolder.validate(prepared)
    native_rename = scaffolder_module._rename_directory_without_replace
    marker = library_root / "RACE/owner-file.txt"

    def race_then_rename(source: Path, destination: Path) -> None:
        destination.mkdir()
        marker.write_text("do not replace", encoding="utf-8")
        native_rename(source, destination)

    monkeypatch.setattr(
        scaffolder_module,
        "_rename_directory_without_replace",
        race_then_rename,
    )

    with pytest.raises(FileExistsError):
        manager.scaffolder.commit(prepared)

    assert marker.read_text(encoding="utf-8") == "do not replace"
    assert prepared.temp_root.is_dir()
    manager.scaffolder.abort(prepared)


def test_switch_requires_database_and_directory_then_updates_settings_atomically(
    project_environment,
) -> None:
    """Catches selection of missing storage and loss of Owner settings on switch."""
    manager, library_root, _, settings_store = project_environment
    manager.create(new_project_command("PROJECT_A"))

    selection = manager.switch("PROJECT_A")

    assert selection.project_id == "PROJECT_A"
    assert selection.project_root == (library_root / "PROJECT_A").resolve()
    assert settings_store.load() == IncubatorSettings(
        owner_name="产品经理",
        library_root=str(library_root.resolve()),
        current_project_id="PROJECT_A",
    )

    with pytest.raises(KeyError, match="project not found"):
        manager.switch("MISSING")

    (library_root / "PROJECT_A").rename(library_root / "PROJECT_A_MISSING")
    with pytest.raises(FileNotFoundError, match="project directory"):
        manager.switch("PROJECT_A")


def test_project_list_summarizes_local_counts(project_environment) -> None:
    """Catches project cards showing counts from another project or stale placeholders."""
    manager, library_root, _, _ = project_environment
    manager.create(new_project_command("PROJECT_A"))
    source_index = library_root / "PROJECT_A/.incubator/source-index.json"
    source_index.write_text(
        json.dumps({"sources": [{"source_id": "SRC-1"}, {"source_id": "SRC-2"}]}),
        encoding="utf-8",
    )
    (library_root / "PROJECT_A/wiki/drafts/DRAFT-1").mkdir()

    summaries = manager.list()

    assert len(summaries) == 1
    assert summaries[0].project_id == "PROJECT_A"
    assert summaries[0].source_count == 2
    assert summaries[0].draft_count == 2
    assert summaries[0].current_version is None


def test_container_composes_project_management_from_initialized_library(tmp_path: Path) -> None:
    """Catches the real app container hiding the project center after Owner setup."""
    from src.application.container import build_container

    application_root = tmp_path / "application"
    config_root = application_root / "config"
    config_root.mkdir(parents=True)
    app_path = config_root / "app.yaml"
    schema_path = config_root / "schema.yaml"
    app_path.write_text(
        """
app:
  name: 产品文档孵化器
  project_id: LLD
  default_query_scope: effective
  max_upload_mb: 20
  accepted_extensions: [pdf, docx, txt, md]
  demo_mode: true
timeouts:
  ingest_seconds: 60
  query_seconds: 30
  lint_seconds: 60
""".strip(),
        encoding="utf-8",
    )
    schema_path.write_text(
        "schema_version: '1.0'\nlint_input_contract_version: '2.0'\n",
        encoding="utf-8",
    )
    library_root = tmp_path / "owner-library"
    JsonIncubatorSettingsStore(library_root).save(
        IncubatorSettings(
            owner_name="产品经理",
            library_root=str(library_root.resolve()),
            current_project_id=None,
        )
    )

    container = build_container(
        app_path,
        schema_path,
        environ={"INCUBATOR_LIBRARY_ROOT": str(library_root)},
    )

    assert container.manage_projects is not None
    assert container.manage_projects.list() == []


def test_initialize_failure_keeps_previous_library_pointer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Catches restart selecting a new library before its settings are durable."""
    from src.application.use_cases.manage_projects import ManageProjects
    from src.infrastructure.files.project_library import ProjectLibraryLocator
    from src.infrastructure.files.project_scaffolder import ProjectScaffolder

    pointer_path = tmp_path / "data/local_state/incubator-root.json"
    original_root = tmp_path / "original-library"
    locator = ProjectLibraryLocator(pointer_path=pointer_path, environ={})
    locator.save_pointer(original_root)
    manager = ManageProjects(
        library_root=original_root,
        projects=SqliteProjectRepository(original_root / ".incubator/product_incubator.db"),
        scaffolder=ProjectScaffolder(
            library_root=original_root,
            schema_source=Path("assets/incubator_schema"),
            now=lambda: NOW,
        ),
        settings=JsonIncubatorSettingsStore(original_root),
        now=lambda: NOW,
        locator=locator,
        schema_source=Path("assets/incubator_schema").resolve(),
    )
    new_root = tmp_path / "new-library"

    def fail_save(_settings):
        raise OSError("settings unavailable")

    original_store_class = JsonIncubatorSettingsStore

    class FailingStore(original_store_class):
        def save(self, settings):
            fail_save(settings)

    monkeypatch.setattr(
        "src.application.use_cases.manage_projects.JsonIncubatorSettingsStore",
        FailingStore,
    )

    with pytest.raises(OSError, match="settings unavailable"):
        manager.initialize("产品经理", new_root)

    assert locator.resolve() == original_root.resolve()
