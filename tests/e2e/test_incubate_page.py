from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest


def _render_incubate_page(
    library_root: str,
    job_mode: str = "idle_then_running",
    start_error: str | None = None,
) -> None:
    import hashlib
    from datetime import UTC, datetime
    from pathlib import Path

    from src.application.container import AppContainer, AppSettings
    from src.application.dto.documents import IncubateDocumentInput
    from src.application.project_context import ProjectContext
    from src.application.use_cases.incubate_document import IncubateDocument
    from src.domain.enums import (
        AuthorityLevel,
        DocumentIncubationJobStatus,
        SecurityLevel,
    )
    from src.domain.incubator import DocumentIncubationJob
    from src.domain.models import Project, SourceRecord
    from src.infrastructure.db.migrations import migrate
    from src.infrastructure.db.repositories import (
        SqliteDocumentDraftRepository,
        SqliteProjectRepository,
        SqliteSourceRepository,
    )
    from src.infrastructure.files.document_store import DocumentStore
    from src.infrastructure.files.project_library import ProjectPaths
    from src.infrastructure.files.wiki_context_reader import WikiContextReader
    from src.infrastructure.observability.model_call_logger import ModelCallLogger
    from src.ui.pages.incubate import render

    class Gateway:
        def generate_draft(self, inputs, *, on_started=None):
            if on_started is not None:
                on_started("TASK-DOCUMENT-001", "WF-UI")
            page = inputs["wiki_pages"][0]
            return {
                "workflow_run_id": "WF-UI",
                "result": {
                    "document_markdown": "# 项目 A 产品方案\n\n## 产品概述\n\n可编辑候选。",
                    "summary": "候选摘要",
                    "missing_sections": [],
                    "evidence_gaps": [],
                    "source_ids": [page["source_id"]],
                    "section_citations": [
                        {
                            "heading": "产品概述",
                            "source_id": page["source_id"],
                            "chunk_id": page["chunk_id"],
                            "locator": page["locator"],
                            "excerpt": page["excerpt"],
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
                ingest_status="ingested",
                source_page_path="wiki/sources/SRC-001-需求.md",
                created_at=now,
            )
        )
    except Exception:
        pass
    source_page = paths.wiki_root / "sources/SRC-001-需求.md"
    source_page.parent.mkdir(parents=True, exist_ok=True)
    source_page.write_text(
        "---\nproject_id: PROJECT_A\nsource_id: SRC-001\nraw_sha256: "
        + hashlib.sha256(payload).hexdigest()
        + "\n---\n# 来源：需求\n\n支持候选文档。\n\n"
        "来源定位【SRC-001：heading:需求; line:1】",
        encoding="utf-8",
    )
    service = IncubateDocument(
        paths=paths,
        projects=projects,
        sources=SqliteSourceRepository(db_path),
        drafts=SqliteDocumentDraftRepository(db_path),
        store=DocumentStore(paths),
        gateway=Gateway(),
        wiki_context=WikiContextReader(
            paths=paths,
            sources=SqliteSourceRepository(db_path),
        ),
        model_call_logger=ModelCallLogger(db_path),
        now=lambda: now,
    )
    drafts = service.list_drafts("PROJECT_A")
    if job_mode == "succeeded" and not drafts:
        service.execute(
            IncubateDocumentInput(
                project_id="PROJECT_A",
                source_ids=["SRC-001"],
                requested_by="Owner",
            )
        )
        drafts = service.list_drafts("PROJECT_A")

    state_path = root / "fake-incubation-state.txt"
    count_path = root / "fake-incubation-start-count.txt"

    def make_job(status: DocumentIncubationJobStatus) -> DocumentIncubationJob:
        lifecycle = {}
        if status in {
            DocumentIncubationJobStatus.RUNNING,
            DocumentIncubationJobStatus.SUCCEEDED,
        }:
            lifecycle.update(
                started_at=now,
                dify_task_id="TASK-DOCUMENT-001",
                workflow_run_id="WF-UI",
            )
        if status is DocumentIncubationJobStatus.SUCCEEDED:
            lifecycle.update(draft_id=drafts[0].id, finished_at=now)
        if status is DocumentIncubationJobStatus.FAILED:
            lifecycle.update(
                error_code="DOCUMENT_INCUBATION_WORKFLOW_FAILED",
                finished_at=now,
            )
        return DocumentIncubationJob(
            id="JOB-UI-001",
            project_id="PROJECT_A",
            source_ids=["SRC-001"],
            requested_by="Owner",
            status=status,
            created_at=now,
            updated_at=now,
            **lifecycle,
        )

    class JobCoordinator:
        def start(self, command):
            del command
            if start_error is not None:
                raise ValueError(start_error)
            previous = int(count_path.read_text(encoding="utf-8")) if count_path.is_file() else 0
            count_path.write_text(str(previous + 1), encoding="utf-8")
            state_path.write_text("running", encoding="utf-8")
            return make_job(DocumentIncubationJobStatus.RUNNING)

        def get_current(self, project_id):
            assert project_id == "PROJECT_A"
            if job_mode == "running" or state_path.is_file():
                return make_job(DocumentIncubationJobStatus.RUNNING)
            if job_mode == "succeeded":
                return make_job(DocumentIncubationJobStatus.SUCCEEDED)
            if job_mode == "failed":
                return make_job(DocumentIncubationJobStatus.FAILED)
            return None

        def get_result(self, job_id):
            assert job_id == "JOB-UI-001"
            return None

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
            document_incubation_jobs=JobCoordinator(),
        )
    )


def test_incubate_page_starts_once_and_shows_running_state(tmp_path: Path) -> None:
    root = tmp_path / "library"
    page = AppTest.from_function(_render_incubate_page, args=(str(root),)).run()

    page.multiselect(key="incubate_source_ids").select("SRC-001")
    page.button(key="incubate_generate").click().run()

    assert not page.exception
    assert "候选产品文档生成中" in "\n".join(item.value for item in page.info)
    assert page.button(key="incubate_generate").disabled is True
    assert (root / "fake-incubation-start-count.txt").read_text(encoding="utf-8") == "1"

    page.run()

    assert (root / "fake-incubation-start-count.txt").read_text(encoding="utf-8") == "1"


def test_incubate_page_recovers_running_job_after_refresh(tmp_path: Path) -> None:
    page = AppTest.from_function(
        _render_incubate_page,
        args=(str(tmp_path / "library"), "running"),
    ).run()

    assert not page.exception
    assert "候选产品文档生成中" in "\n".join(item.value for item in page.info)
    assert page.button(key="incubate_generate").disabled is True


def test_incubate_page_recovers_succeeded_job_and_shows_candidate(tmp_path: Path) -> None:
    page = AppTest.from_function(
        _render_incubate_page,
        args=(str(tmp_path / "library"), "succeeded"),
    ).run()

    assert not page.exception
    assert "候选产品文档已生成" in "\n".join(item.value for item in page.success)
    assert page.text_area(
        key=next(item.key for item in page.text_area if item.key.startswith("draft_edit_"))
    )


def test_incubate_page_failed_job_shows_safe_code_and_allows_retry(tmp_path: Path) -> None:
    page = AppTest.from_function(
        _render_incubate_page,
        args=(str(tmp_path / "library"), "failed"),
    ).run()

    assert not page.exception
    assert "DOCUMENT_INCUBATION_WORKFLOW_FAILED" in "\n".join(item.value for item in page.error)

    page.multiselect(key="incubate_source_ids").select("SRC-001").run()

    assert page.button(key="incubate_generate").disabled is False


def test_incubate_page_preserves_safe_start_failure_detail(tmp_path: Path) -> None:
    """Catches actionable startup failures being collapsed into an opaque code."""
    page = AppTest.from_function(
        _render_incubate_page,
        args=(str(tmp_path / "library"), "idle", "SOURCE_SELECTION_STALE"),
    ).run()

    page.multiselect(key="incubate_source_ids").select("SRC-001")
    page.button(key="incubate_generate").click().run()

    assert not page.exception
    assert "DOCUMENT_INCUBATION_START_FAILED:SOURCE_SELECTION_STALE" in "\n".join(
        item.value for item in page.error
    )
