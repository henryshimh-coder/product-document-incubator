from __future__ import annotations

import pytest
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


def _render_materials_ingest_page(library_root: str, status: str) -> None:
    import json
    from pathlib import Path as path_type

    from src.application.container import AppContainer, AppSettings
    from src.application.dto.wiki_ingest import WikiIngestResultView
    from src.application.project_context import ProjectContext
    from src.domain.wiki import WikiIngestStatus
    from src.infrastructure.files.project_library import ProjectPaths
    from src.ui.pages.materials import render

    class SuccessfulIngest:
        def execute(self, command):
            return WikiIngestResultView(
                source_id=command.source_id,
                status=WikiIngestStatus.INGESTED,
                source_page_path=f"wiki/sources/{command.source_id}-material.md",
                topic_page_paths=[f"wiki/topics/{command.source_id}-topic-1.md"],
                conflict_count=0,
                evidence_gap_count=0,
            )

    root = path_type(library_root)
    paths = ProjectPaths.for_project(root, "PROJECT_A")
    paths.system_root.mkdir(parents=True, exist_ok=True)
    (paths.system_root / "source-index.json").write_text(
        json.dumps(
            {
                "schema_version": "2.2",
                "project_id": "PROJECT_A",
                "sources": [
                    {
                        "source_id": "SRC-PROJECT-A-001",
                        "material_name": "产品原则",
                        "material_version": "1.0",
                        "sha256": "a" * 64,
                        "source_type": "product_requirement",
                        "security_level": "L2",
                        "archive_path": "raw/2026/SRC-PROJECT-A-001/material.md",
                        "ingest_status": status,
                        "ingest_error_code": (
                            "MODEL_TIMEOUT" if status == "ingest_failed" else None
                        ),
                        "source_page_path": (
                            "wiki/sources/SRC-PROJECT-A-001-material.md"
                            if status == "ingested"
                            else None
                        ),
                    }
                ],
            }
        ),
        encoding="utf-8",
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
            active_project=ProjectContext(
                "PROJECT_A", paths, root / ".incubator/product_incubator.db"
            ),
            archive_raw_source=object(),
            wiki_ingest=SuccessfulIngest(),
        )
    )


def test_materials_page_renders_a_nonwriting_confirmation_form(tmp_path) -> None:
    """Catches a page visit creating a material archive before Owner confirmation."""
    page = AppTest.from_function(
        _render_materials_page,
        args=(str(tmp_path / "library"), str(tmp_path / "unused.md")),
    ).run()

    assert not page.exception
    assert page.button(key="materials_archive")


def test_materials_page_uses_confirmed_browser_upload_instead_of_local_path(tmp_path) -> None:
    """Catches the 2.1 material workflow asking an Owner to type a device-specific path."""
    page = AppTest.from_function(
        _render_materials_page,
        args=(str(tmp_path / "library"), str(tmp_path / "unused.md")),
    ).run()

    with pytest.raises(KeyError):
        page.text_input(key="materials_local_path")
    assert page.file_uploader(key="materials_upload")


def test_material_page_runs_ingest_after_owner_click(tmp_path) -> None:
    """Catches the pending action failing to invoke the application service."""
    page = AppTest.from_function(
        _render_materials_ingest_page,
        args=(str(tmp_path / "library"), "pending_ingest"),
    ).run()

    button = page.button(key="material_ingest_SRC-PROJECT-A-001")
    assert button
    button.click().run()

    assert page.success
    assert any("已 Ingest" in item.value for item in page.markdown)


@pytest.mark.parametrize(
    ("status", "button_key", "disabled", "expected_text"),
    [
        ("ingesting", "material_ingesting_SRC-PROJECT-A-001", True, "处理中"),
        ("ingested", None, False, "查看 Wiki 结果"),
        ("ingest_failed", "material_reingest_SRC-PROJECT-A-001", False, "MODEL_TIMEOUT"),
        (
            "reingest_recommended",
            "material_reingest_SRC-PROJECT-A-001",
            False,
            "明确重新 Ingest",
        ),
    ],
)
def test_material_page_renders_wiki_ingest_lifecycle(
    tmp_path, status, button_key, disabled, expected_text
) -> None:
    """Catches a lifecycle state exposing the wrong Owner action."""
    page = AppTest.from_function(
        _render_materials_ingest_page,
        args=(str(tmp_path / status), status),
    ).run()

    assert not page.exception
    rendered = "\n".join(item.value for item in (*page.markdown, *page.caption, *page.info))
    rendered += "\n" + "\n".join(item.label for item in page.button)
    assert expected_text in rendered
    if button_key is not None:
        assert page.button(key=button_key).disabled is disabled
