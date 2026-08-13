from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest


def _render_incubate_page(library_root: str) -> None:
    import hashlib
    from datetime import UTC, datetime
    from pathlib import Path

    from src.application.container import AppContainer, AppSettings
    from src.application.project_context import ProjectContext
    from src.application.use_cases.incubate_document import IncubateDocument
    from src.domain.enums import AuthorityLevel, SecurityLevel
    from src.domain.models import Project, SourceRecord
    from src.infrastructure.db.migrations import migrate
    from src.infrastructure.db.repositories import (
        SqliteDocumentDraftRepository,
        SqliteProjectRepository,
        SqliteSourceRepository,
    )
    from src.infrastructure.files.document_store import DocumentStore
    from src.infrastructure.files.project_library import ProjectPaths
    from src.infrastructure.observability.model_call_logger import ModelCallLogger
    from src.ui.pages.incubate import render

    class Gateway:
        def generate_draft(self, inputs):
            fragment = inputs["source_fragments"][0]
            return {
                "workflow_run_id": "WF-UI",
                "result": {
                    "document_markdown": "# 项目 A 产品方案\n\n## 产品概述\n\n可编辑候选。",
                    "summary": "候选摘要",
                    "missing_sections": [],
                    "evidence_gaps": [],
                    "source_ids": [fragment["source_id"]],
                    "section_citations": [
                        {
                            "heading": "产品概述",
                            "source_id": fragment["source_id"],
                            "chunk_id": fragment["chunk_id"],
                            "locator": fragment["locator"],
                            "excerpt": fragment["excerpt"],
                        }
                    ],
                },
            }

    root = Path(library_root)
    paths = ProjectPaths.for_project(root, "PROJECT_A")
    paths.raw_root.mkdir(parents=True, exist_ok=True)
    paths.wiki_root.mkdir(parents=True, exist_ok=True)
    paths.schema_root.mkdir(parents=True, exist_ok=True)
    paths.system_root.mkdir(parents=True, exist_ok=True)
    (paths.wiki_root / "log.md").write_text("# 项目日志\n", encoding="utf-8")
    (paths.schema_root / "product-document-template.md").write_text(
        "# 项目 A 产品方案\n\n## 产品概述\n", encoding="utf-8"
    )
    db_path = root / ".incubator/product_incubator.db"
    migrate(db_path)
    now = datetime(2026, 8, 12, tzinfo=UTC)
    projects = SqliteProjectRepository(db_path)
    try:
        projects.add(
            Project(
                id="PROJECT_A",
                name="项目 A",
                product_line="测试",
                stage="待初始化",
                current_baseline_id=None,
                allow_external_model=True,
                created_at=now,
                updated_at=now,
            )
        )
    except Exception:
        pass
    path = paths.raw_root / "2026" / "SRC-001" / "需求.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# 需求\n\n支持候选文档。", encoding="utf-8")
    payload = path.read_bytes()
    try:
        SqliteSourceRepository(db_path).add(
            SourceRecord(
                id="SRC-001",
                project_id="PROJECT_A",
                original_filename="需求.md",
                archive_path=str(path),
                sha256=hashlib.sha256(payload).hexdigest(),
                mime_type="text/plain",
                size_bytes=len(payload),
                source_type="product_requirement",
                authority_level=AuthorityLevel.FORMAL_DECISION,
                source_department="产品部",
                provider=None,
                document_date=now.date(),
                document_version="v1.0",
                applicable_baseline_version="未关联基线",
                security_level=SecurityLevel.L2_INTERNAL,
                is_redacted=True,
                allow_external_model=True,
                is_sandbox=False,
                ingest_status="archived",
                created_at=now,
            )
        )
    except Exception:
        pass
    service = IncubateDocument(
        paths=paths,
        projects=projects,
        sources=SqliteSourceRepository(db_path),
        drafts=SqliteDocumentDraftRepository(db_path),
        store=DocumentStore(paths),
        gateway=Gateway(),
        model_call_logger=ModelCallLogger(db_path),
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
            incubate_document=service,
        )
    )


def test_incubate_page_generates_an_editable_candidate(tmp_path: Path) -> None:
    page = AppTest.from_function(_render_incubate_page, args=(str(tmp_path / "library"),)).run()

    page.multiselect(key="incubate_source_ids").select("SRC-001")
    page.button(key="incubate_generate").click().run()

    assert not page.exception
    assert "已生成候选版本" in "\n".join(item.value for item in page.success)
    assert page.text_area(
        key=next(item.key for item in page.text_area if item.key.startswith("draft_edit_"))
    )
