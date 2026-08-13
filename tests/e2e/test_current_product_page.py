from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest


def _render_current_product_page(library_root: str) -> None:
    from datetime import UTC, datetime
    from pathlib import Path

    from src.application.container import AppContainer, AppSettings
    from src.application.dto.documents import ExportedDocument
    from src.application.project_context import ProjectContext
    from src.domain.models import BaselineManifest
    from src.infrastructure.files.manifest_store import ManifestStore
    from src.infrastructure.files.project_library import ProjectPaths
    from src.ui.pages.current_product import render

    class Exporter:
        def execute(self, _command):
            return ExportedDocument(
                filename="项目 A_产品方案_1.0.md",
                content="# 项目 A 产品方案\n\n## 产品概述\n\n当前内容。".encode(),
                sha256="a" * 64,
                export_path=Path(library_root) / "exports/项目 A_产品方案_1.0.md",
            )

    root = Path(library_root)
    paths = ProjectPaths.for_project(root, "PROJECT_A")
    paths.project_root.mkdir(parents=True, exist_ok=True)
    paths.system_root.mkdir(parents=True, exist_ok=True)
    ManifestStore(paths.manifest_path, project_root=paths.project_root).atomic_replace(
        BaselineManifest(
            schema_version="2.0",
            project_id="PROJECT_A",
            current_baseline_id="BASE-PROJECT_A-01",
            current_version="PROJECT_A-01",
            parent_baseline_id=None,
            full_document_path="wiki/versions/PROJECT_A-01/产品方案.md",
            card_snapshot_path="wiki/versions/PROJECT_A-01/cards.json",
            full_document_sha256="a" * 64,
            card_snapshot_sha256="b" * 64,
            change_request_id=None,
            approved_by="Owner",
            published_at=datetime(2026, 8, 12, tzinfo=UTC),
            display_version="1.0",
        )
    )
    render(
        AppContainer(
            settings=AppSettings(
                name="产品文档孵化器",
                project_id="PROJECT_A",
                default_query_scope="effective",
                max_upload_mb=20,
                accepted_extensions=("md",),
                demo_mode=True,
                schema_version="2.0",
            ),
            active_project=ProjectContext("PROJECT_A", paths, root / "state.db"),
            export_current_document=Exporter(),
        )
    )


def test_current_product_page_offers_one_markdown_download(tmp_path: Path) -> None:
    page = AppTest.from_function(
        _render_current_product_page,
        args=(str(tmp_path / "library"),),
    ).run()

    assert not page.exception
    assert page.download_button(key="current_product_download")
    assert "当前内容" in "\n".join(item.value for item in page.markdown)
