from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest


def _render_projects_page(library_root: str) -> None:
    from datetime import UTC, datetime
    from pathlib import Path

    from src.application.container import AppContainer, AppSettings
    from src.application.use_cases.manage_projects import ManageProjects
    from src.domain.incubator import IncubatorSettings
    from src.infrastructure.db.migrations import migrate
    from src.infrastructure.db.repositories import SqliteProjectRepository
    from src.infrastructure.files.project_library import JsonIncubatorSettingsStore
    from src.infrastructure.files.project_scaffolder import ProjectScaffolder
    from src.ui.pages.projects import render

    root = Path(library_root)
    database_path = root / ".incubator/product_incubator.db"
    migrate(database_path)
    settings = JsonIncubatorSettingsStore(root)
    if settings.load() is None:
        settings.save(
            IncubatorSettings(
                owner_name="产品经理",
                library_root=str(root.resolve()),
                current_project_id=None,
            )
        )
    manager = ManageProjects(
        library_root=root,
        projects=SqliteProjectRepository(database_path),
        scaffolder=ProjectScaffolder(
            library_root=root,
            schema_source=Path("assets/incubator_schema"),
            now=lambda: datetime(2026, 8, 12, tzinfo=UTC),
        ),
        settings=settings,
        now=lambda: datetime(2026, 8, 12, tzinfo=UTC),
    )
    render(
        AppContainer(
            settings=AppSettings(
                name="产品文档孵化器",
                project_id="LLD",
                default_query_scope="effective",
                max_upload_mb=20,
                accepted_extensions=("pdf", "docx", "txt", "md"),
                demo_mode=True,
                schema_version="1.0",
            ),
            manage_projects=manager,
        )
    )


def _seed_project(library_root: Path, project_id: str) -> None:
    from datetime import UTC, datetime

    from src.application.dto.projects import CreateProjectInput
    from src.application.use_cases.manage_projects import ManageProjects
    from src.infrastructure.db.migrations import migrate
    from src.infrastructure.db.repositories import SqliteProjectRepository
    from src.infrastructure.files.project_library import JsonIncubatorSettingsStore
    from src.infrastructure.files.project_scaffolder import ProjectScaffolder

    db_path = library_root / ".incubator/product_incubator.db"
    migrate(db_path)
    manager = ManageProjects(
        library_root=library_root,
        projects=SqliteProjectRepository(db_path),
        scaffolder=ProjectScaffolder(
            library_root=library_root,
            schema_source=Path("assets/incubator_schema"),
            now=lambda: datetime(2026, 8, 12, tzinfo=UTC),
        ),
        settings=JsonIncubatorSettingsStore(library_root),
        now=lambda: datetime(2026, 8, 12, tzinfo=UTC),
    )
    manager.create(
        CreateProjectInput(
            project_id=project_id,
            name=f"{project_id} 产品",
            description="产品文档孵化项目",
            initial_display_version=None,
            allow_external_model=False,
        )
    )


def _render_uninitialized_projects_page(library_root: str) -> None:
    from datetime import UTC, datetime
    from pathlib import Path

    from src.application.container import AppContainer, AppSettings
    from src.application.use_cases.manage_projects import ManageProjects
    from src.infrastructure.db.repositories import SqliteProjectRepository
    from src.infrastructure.files.project_library import JsonIncubatorSettingsStore
    from src.infrastructure.files.project_scaffolder import ProjectScaffolder
    from src.ui.pages.projects import render

    root = Path(library_root)
    manager = ManageProjects(
        library_root=root,
        projects=SqliteProjectRepository(root / ".incubator/product_incubator.db"),
        scaffolder=ProjectScaffolder(
            library_root=root,
            schema_source=Path("assets/incubator_schema"),
            now=lambda: datetime(2026, 8, 12, tzinfo=UTC),
        ),
        settings=JsonIncubatorSettingsStore(root),
        now=lambda: datetime(2026, 8, 12, tzinfo=UTC),
    )
    render(
        AppContainer(
            settings=AppSettings(
                name="产品文档孵化器",
                project_id="LLD",
                default_query_scope="effective",
                max_upload_mb=20,
                accepted_extensions=("pdf", "docx", "txt", "md"),
                demo_mode=True,
                schema_version="1.0",
            ),
            manage_projects=manager,
        )
    )


def test_first_setup_immediately_enters_project_center(tmp_path: Path) -> None:
    """Catches Owner setup requiring an unexplained second interaction before use."""
    library_root = tmp_path / "library"
    page = AppTest.from_function(
        _render_uninitialized_projects_page,
        args=(str(library_root),),
    ).run()
    page.text_input(key="incubator_setup_owner").input("产品经理")
    page.button(key="incubator_setup_submit").click().run()

    assert not page.exception
    assert page.button(key="projects_create_submit").label == "创建项目"
    assert (library_root / ".incubator/settings.json").is_file()
    assert (library_root / ".incubator/product_incubator.db").is_file()


def test_projects_page_has_empty_guidance_and_one_primary_create_action(tmp_path: Path) -> None:
    """Catches project onboarding without a clear next step or competing main actions."""
    page = AppTest.from_function(_render_projects_page, args=(str(tmp_path / "library"),)).run()

    assert not page.exception
    assert any("新建第一个产品项目" in item.value for item in page.markdown)
    assert page.button(key="projects_create_submit").label == "创建项目"
    assert sum(button.proto.type == "primary" for button in page.button) == 1


def test_projects_page_creates_card_with_local_path_and_project_scoped_key(
    tmp_path: Path,
) -> None:
    """Catches the create form failing to surface the durable local project."""
    library_root = tmp_path / "library"
    page = AppTest.from_function(_render_projects_page, args=(str(library_root),)).run()
    page.text_input(key="projects_create_id").input("NEW_PRODUCT")
    page.text_input(key="projects_create_name").input("新产品")
    page.text_area(key="projects_create_description").input("真实场景产品文档孵化")
    page.button(key="projects_create_submit").click().run()

    assert not page.exception
    assert (library_root / "NEW_PRODUCT/schema/AGENTS.md").is_file()
    assert page.button(key="project_open_NEW_PRODUCT").label == "进入项目"
    rendered = "\n".join(item.value for item in page.markdown)
    assert str((library_root / "NEW_PRODUCT").resolve()) in rendered


def _create_from_page(page: AppTest, project_id: str, parent_root: Path) -> None:
    parent_root.mkdir()
    page.text_input(key="projects_create_id").input(project_id)
    page.text_input(key="projects_create_name").input(f"{project_id} 产品")
    page.text_area(key="projects_create_description").input("独立目录项目文档孵化")
    page.text_input(key="projects_create_parent_root").input(str(parent_root))
    page.button(key="projects_create_submit").click().run()


def test_owner_creates_projects_in_two_independent_parents(tmp_path: Path) -> None:
    """Catches the page ignoring an Owner-selected parent directory."""
    page = AppTest.from_function(_render_projects_page, args=(str(tmp_path / "library"),)).run()

    _create_from_page(page, "PROJECT_A", tmp_path / "one")
    _create_from_page(page, "PROJECT_B", tmp_path / "two")

    assert (tmp_path / "one/PROJECT_A/README.md").is_file()
    assert (tmp_path / "two/PROJECT_B/README.md").is_file()


def test_unavailable_project_offers_relocation_instead_of_open(tmp_path: Path) -> None:
    """Catches a missing registered root still exposing the unsafe open action."""
    library_root = tmp_path / "library"
    _seed_project(library_root, "PROJECT_A")
    (library_root / "PROJECT_A").rename(library_root / "PROJECT_A_MOVED")

    page = AppTest.from_function(_render_projects_page, args=(str(library_root),)).run()

    assert page.button(key="project_relocate_PROJECT_A")
    assert "project_open_PROJECT_A" not in {button.key for button in page.button}


def test_switch_project_clears_query_release_and_upload_session_state(tmp_path: Path) -> None:
    """Catches one project's decisions, uploads, or query result leaking into another."""
    library_root = tmp_path / "library"
    _seed_project(library_root, "PROJECT_A")
    _seed_project(library_root, "PROJECT_B")
    page = AppTest.from_function(_render_projects_page, args=(str(library_root),)).run()
    page.session_state["query_result"] = {"answer": "A"}
    page.session_state["release_confirm"] = {"project_id": "PROJECT_A"}
    page.session_state["ingest_uploaded_file"] = b"A"
    page.session_state["_pi_home_page"] = "PAGE_OBJECT"

    page.button(key="project_open_PROJECT_B").click().run()

    assert not page.exception
    assert page.session_state["active_project_id"] == "PROJECT_B"
    assert page.session_state["incubator_owner"] == "产品经理"
    assert "query_result" not in page.session_state
    assert "release_confirm" not in page.session_state
    assert "ingest_uploaded_file" not in page.session_state
    assert page.session_state["_pi_home_page"] == "PAGE_OBJECT"
