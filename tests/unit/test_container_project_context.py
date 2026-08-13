from __future__ import annotations

from pathlib import Path

import pytest

from src.application.container import AppContainer, AppSettings


def test_container_without_active_project_exposes_only_project_management() -> None:
    """Catches operational services being available before an Owner selects a project."""
    container = AppContainer(
        settings=AppSettings(
            name="产品文档孵化器",
            project_id="LLD",
            default_query_scope="effective",
            max_upload_mb=20,
            accepted_extensions=("md",),
            demo_mode=True,
            schema_version="1.0",
        ),
        manage_projects=object(),
    )

    assert container.active_project is None
    assert container.manage_projects is not None
    assert container.query is None
    with pytest.raises(RuntimeError, match="active project"):
        container.require_project_id()


def test_container_uses_owner_selected_project_as_the_runtime_context(tmp_path) -> None:
    """Catches runtime composition falling back to the legacy configured project ID."""
    from datetime import UTC, datetime

    from src.application.container import build_container
    from src.application.dto.projects import CreateProjectInput
    from src.application.use_cases.manage_projects import ManageProjects
    from src.domain.incubator import IncubatorSettings
    from src.infrastructure.db.migrations import migrate
    from src.infrastructure.db.repositories import SqliteProjectRepository
    from src.infrastructure.files.project_library import JsonIncubatorSettingsStore
    from src.infrastructure.files.project_scaffolder import ProjectScaffolder

    config = tmp_path / "config"
    config.mkdir()
    app_path = config / "app.yaml"
    schema_path = config / "schema.yaml"
    app_path.write_text(
        """
app:
  name: 产品文档孵化器
  project_id: LEGACY
  default_query_scope: effective
  max_upload_mb: 20
  accepted_extensions: [md]
  demo_mode: true
timeouts:
  ingest_seconds: 60
  query_seconds: 30
  lint_seconds: 60
""".strip(),
        encoding="utf-8",
    )
    schema_path.write_text("schema_version: '1.0'\n", encoding="utf-8")
    library = tmp_path / "library"
    settings = JsonIncubatorSettingsStore(library)
    settings.save(
        IncubatorSettings(
            owner_name="产品经理",
            library_root=str(library.resolve()),
            current_project_id=None,
        )
    )
    db_path = library / ".incubator/product_incubator.db"
    migrate(db_path)
    manager = ManageProjects(
        library_root=library,
        projects=SqliteProjectRepository(db_path),
        scaffolder=ProjectScaffolder(
            library_root=library,
            schema_source=Path("assets/incubator_schema"),
            now=lambda: datetime(2026, 8, 12, tzinfo=UTC),
        ),
        settings=settings,
        now=lambda: datetime(2026, 8, 12, tzinfo=UTC),
    )
    manager.create(
        CreateProjectInput(
            project_id="PROJECT_A",
            name="项目 A",
            description="隔离测试",
            initial_display_version=None,
            allow_external_model=False,
        )
    )
    manager.switch("PROJECT_A")

    container = build_container(
        app_path,
        schema_path,
        environ={"INCUBATOR_LIBRARY_ROOT": str(library)},
    )

    assert container.require_project_id() == "PROJECT_A"
    assert container.active_project is not None
    assert container.active_project.paths.project_root == (library / "PROJECT_A").resolve()
