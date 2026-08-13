from __future__ import annotations

from streamlit.testing.v1 import AppTest


def _render_materials_page(library_root: str, source_path: str) -> None:
    from datetime import UTC as timezone_utc
    from datetime import datetime as datetime_type
    from pathlib import Path as path_type

    from src.application.container import AppContainer, AppSettings
    from src.application.project_context import ProjectContext
    from src.application.use_cases.archive_raw_source import ArchiveRawSource
    from src.domain.models import Project
    from src.infrastructure.db.migrations import migrate
    from src.infrastructure.db.repositories import SqliteProjectRepository, SqliteSourceRepository
    from src.infrastructure.files.project_library import ProjectPaths
    from src.infrastructure.files.project_source_archive import ProjectSourceArchive
    from src.infrastructure.files.source_index_store import SourceIndexStore
    from src.ui.pages.materials import render

    root = path_type(library_root)
    paths = ProjectPaths.for_project(root, "PROJECT_A")
    paths.raw_root.mkdir(parents=True, exist_ok=True)
    db_path = root / ".incubator/product_incubator.db"
    migrate(db_path)
    now = datetime_type(2026, 8, 12, tzinfo=timezone_utc)
    projects = SqliteProjectRepository(db_path)
    try:
        projects.add(
            Project(
                id="PROJECT_A",
                name="项目 A",
                product_line="测试",
                stage="待初始化",
                current_baseline_id=None,
                allow_external_model=False,
                created_at=now,
                updated_at=now,
            )
        )
    except Exception:
        pass
    service = ArchiveRawSource(
        paths=paths,
        sources=SqliteSourceRepository(db_path),
        archive_factory=lambda source_id, year: ProjectSourceArchive(
            paths=paths, source_id=source_id, year=year
        ),
        index=SourceIndexStore(paths),
        now=lambda: now,
    )
    render(
        AppContainer(
            settings=AppSettings(
                name="产品文档孵化器",
                project_id="LLD",
                default_query_scope="effective",
                max_upload_mb=20,
                accepted_extensions=("md",),
                demo_mode=True,
                schema_version="1.0",
            ),
            active_project=ProjectContext("PROJECT_A", paths, db_path),
            archive_raw_source=service,
        )
    )


def test_materials_page_archives_a_local_file_and_shows_project_path(tmp_path) -> None:
    """Catches the materials UI accepting a file without persisting a visible local archive."""
    source = tmp_path / "需求.md"
    source.write_text("# 需求\n", encoding="utf-8")
    page = AppTest.from_function(
        _render_materials_page,
        args=(str(tmp_path / "library"), str(source)),
    ).run()

    page.text_input(key="materials_local_path").input(str(source))
    page.button(key="materials_archive").click().run()

    assert not page.exception
    rendered = "\n".join(item.value for item in page.markdown)
    assert "SRC-PROJECT_A" in rendered
    assert "PROJECT_A" in "\n".join(item.value for item in page.code)
