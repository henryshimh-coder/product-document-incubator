from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest


def _render_checks_page(library_root: str) -> None:
    from pathlib import Path

    from src.application.container import AppContainer, AppSettings
    from src.application.project_context import ProjectContext
    from src.domain.enums import StructureSuggestionStatus
    from src.domain.incubator import StructureSuggestion
    from src.infrastructure.files.project_library import ProjectPaths
    from src.ui.pages.checks import render

    class Suggestions:
        def execute(self, _command):
            return []

        def list(self, _project_id):
            return [
                StructureSuggestion(
                    id="SUG-001",
                    project_id="PROJECT_A",
                    title="风险边界",
                    reason="参考项目包含此章节。",
                    reference_project_ids=["PROJECT_B"],
                    confidence=0.9,
                    status=StructureSuggestionStatus.OPEN,
                    created_at=__import__("datetime").datetime(2026, 8, 12),
                    updated_at=__import__("datetime").datetime(2026, 8, 12),
                )
            ]

        def accept(self, **_kwargs):
            return self.list("PROJECT_A")[0]

        def ignore(self, **_kwargs):
            return self.list("PROJECT_A")[0]

    class Projects:
        def list(self):
            return [
                type("ProjectSummary", (), {"project_id": "PROJECT_A", "name": "项目 A"})(),
                type("ProjectSummary", (), {"project_id": "PROJECT_B", "name": "项目 B"})(),
            ]

    root = Path(library_root)
    paths = ProjectPaths.for_project(root, "PROJECT_A")
    paths.project_root.mkdir(parents=True, exist_ok=True)
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
            manage_projects=Projects(),
            suggest_document_structure=Suggestions(),
        )
    )


def test_checks_page_shows_authorized_structure_suggestions(tmp_path: Path) -> None:
    page = AppTest.from_function(_render_checks_page, args=(str(tmp_path / "library"),)).run()

    assert not page.exception
    assert page.multiselect(key="structure_reference_projects")
    assert "风险边界" in "\n".join(item.value for item in page.markdown)
