from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from src.application.dto.documents import (
    ArchiveRawSourceInput,
    ExportCurrentDocumentInput,
    IncubateDocumentInput,
    PublishDocumentDraftInput,
)
from src.application.dto.projects import CreateProjectInput
from src.application.dto.wiki_ingest import IngestArchivedSourceInput
from src.application.use_cases.archive_raw_source import ArchiveRawSource
from src.application.use_cases.export_current_document import ExportCurrentDocument
from src.application.use_cases.incubate_document import IncubateDocument
from src.application.use_cases.ingest_archived_source import IngestArchivedSource
from src.application.use_cases.manage_projects import ManageProjects
from src.application.use_cases.publish_document_draft import PublishDocumentDraft
from src.domain.enums import AuthorityLevel, SecurityLevel
from src.infrastructure.db.migrations import migrate
from src.infrastructure.db.repositories import (
    SqliteDocumentDraftRepository,
    SqliteProjectRepository,
    SqliteSourceRepository,
    SqliteWikiIngestRunRepository,
)
from src.infrastructure.files.document_store import DocumentStore
from src.infrastructure.files.manifest_store import ManifestStore
from src.infrastructure.files.project_library import JsonIncubatorSettingsStore, ProjectPaths
from src.infrastructure.files.project_scaffolder import ProjectScaffolder
from src.infrastructure.files.project_source_archive import ProjectSourceArchive
from src.infrastructure.files.source_index_store import SourceIndexStore
from src.infrastructure.files.wiki_context_reader import WikiContextReader
from src.infrastructure.gateways.schemas import WikiIngestWorkflowOutput
from src.infrastructure.observability.model_call_logger import ModelCallLogger
from src.infrastructure.recovery.reconciliation_service import ReconciliationService

NOW = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)


class IncubatorHarness:
    def __init__(self, tmp_path: Path) -> None:
        self.library_root = tmp_path / "library"
        self.db_path = self.library_root / ".incubator/product_incubator.db"
        migrate(self.db_path)
        self.projects = SqliteProjectRepository(self.db_path)
        self.manager = ManageProjects(
            library_root=self.library_root,
            projects=self.projects,
            scaffolder=ProjectScaffolder(
                library_root=self.library_root,
                schema_source=Path("assets/incubator_schema").resolve(),
                now=lambda: NOW,
            ),
            settings=JsonIncubatorSettingsStore(self.library_root),
            now=lambda: NOW,
        )
        self.manager.initialize("Owner", self.library_root)

    def create_project(self, project_id: str, name: str) -> ProjectPaths:
        self.manager.create(
            CreateProjectInput(
                project_id=project_id,
                name=name,
                description=f"{name} 产品",
                initial_display_version=None,
                allow_external_model=True,
            )
        )
        return ProjectPaths.for_project(self.library_root, project_id)

    def archive(self, paths: ProjectPaths, filename: str, payload: bytes):
        local_path = paths.library_root.parent / f"{paths.project_id}-{filename}"
        local_path.write_bytes(payload)
        return ArchiveRawSource(
            paths=paths,
            sources=SqliteSourceRepository(self.db_path),
            archive_factory=lambda source_id, year: ProjectSourceArchive(
                paths=paths, source_id=source_id, year=year
            ),
            index=SourceIndexStore(paths),
            now=lambda: NOW,
        ).execute(
            ArchiveRawSourceInput(
                project_id=paths.project_id,
                local_path=local_path,
                source_type="product_requirement",
                authority_level=AuthorityLevel.FORMAL_EFFECTIVE,
                source_department="产品部",
                document_date=date(2026, 8, 12),
                document_version="1.0",
                security_level=SecurityLevel.L2_INTERNAL,
                is_redacted_confirmed=True,
                allow_external_model=True,
            )
        )

    def incubate(self, paths: ProjectPaths, source_id: str):
        service = IncubateDocument(
            paths=paths,
            projects=self.projects,
            sources=SqliteSourceRepository(self.db_path),
            drafts=SqliteDocumentDraftRepository(self.db_path),
            store=DocumentStore(paths),
            gateway=_Gateway(),
            wiki_context=WikiContextReader(
                paths=paths,
                sources=SqliteSourceRepository(self.db_path),
            ),
            model_call_logger=ModelCallLogger(self.db_path),
            now=lambda: NOW,
        )
        draft = service.execute(
            IncubateDocumentInput(
                project_id=paths.project_id,
                source_ids=[source_id],
                requested_by="Owner",
            )
        ).draft
        return service.save_draft(
            paths.project_id,
            draft.id,
            (paths.project_root / draft.markdown_path).read_text(encoding="utf-8"),
        )

    def ingest(self, paths: ProjectPaths, source_id: str) -> None:
        result = IngestArchivedSource(
            paths=paths,
            db_path=self.db_path,
            sources=SqliteSourceRepository(self.db_path),
            runs=SqliteWikiIngestRunRepository(self.db_path),
            gateway=_WikiGateway(),
            customer_names=(),
            strategy_terms=(),
            financial_terms=(),
            leader_names=(),
            unpublished_decisions=(),
            now=lambda: NOW,
        ).execute(
            IngestArchivedSourceInput(
                project_id=paths.project_id,
                source_id=source_id,
                requested_by="Owner",
            )
        )
        assert result.status.value == "ingested"

    def publish(self, paths: ProjectPaths, draft_id: str):
        manifest = ManifestStore(paths.manifest_path, project_root=paths.project_root)
        return PublishDocumentDraft(
            paths=paths,
            projects=self.projects,
            sources=SqliteSourceRepository(self.db_path),
            drafts=SqliteDocumentDraftRepository(self.db_path),
            store=DocumentStore(paths),
            manifest=manifest,
            reconciliation=ReconciliationService(
                manifest_store=manifest,
                db_path=self.db_path,
                project_root=paths.project_root,
            ),
            now=lambda: NOW,
        ).execute(
            PublishDocumentDraftInput(
                project_id=paths.project_id,
                draft_id=draft_id,
                owner_name="Owner",
                display_version="1_0",
            )
        )

    def export(self, paths: ProjectPaths):
        return ExportCurrentDocument(
            paths=paths,
            projects=self.projects,
            manifest=ManifestStore(paths.manifest_path, project_root=paths.project_root),
        ).execute(ExportCurrentDocumentInput(project_id=paths.project_id))


class _Gateway:
    def generate_draft(self, inputs: dict, *, on_started=None) -> dict:
        if on_started is not None:
            on_started("TASK-DOCUMENT-001", f"WF-{inputs['project_id']}")
        page = inputs["wiki_pages"][0]
        return {
            "workflow_run_id": f"WF-{inputs['project_id']}",
            "result": {
                "document_markdown": (
                    f"# {inputs['project_name']} 产品方案\n\n## 产品概述\n\n"
                    "基于已 Ingest 的 Wiki 来源生成候选。"
                ),
                "summary": "由 Owner 材料生成的候选。",
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


class _WikiGateway:
    def generate(self, inputs, **kwargs):
        return WikiIngestWorkflowOutput(
            schema_version="2.2",
            task_id=inputs["task_id"],
            source_page_markdown=(
                "# 来源摘要\n\n"
                "该材料明确了产品建设目标、适用范围、目标用户与核心使用场景，"
                "并描述了需要解决的业务问题和预期价值。\n\n"
                "材料进一步说明了主要功能边界、关键业务流程、输入输出关系、"
                "异常处理原则以及各环节的责任分工。\n\n"
                "在交付要求方面，材料给出了数据使用约束、安全合规要求、"
                "实施依赖、验收口径和后续迭代方向，可作为候选产品文档的依据。\n\n"
                "本次整理仅归纳归档材料中已经明确的信息，不补写材料未提供的事实。"
            ),
            topic_changes=[],
            conflicts=[],
            evidence_gaps=[],
        )


def test_two_projects_complete_isolated_incubation_flows(tmp_path: Path) -> None:
    harness = IncubatorHarness(tmp_path)
    project_a = harness.create_project("PROJECT_A", "产品 A")
    project_b = harness.create_project("PROJECT_B", "产品 B")
    source_a = harness.archive(project_a, "A需求.md", ("A需求\n\nA内容\n" * 10_000).encode())
    source_b = harness.archive(project_b, "B需求.md", ("B需求\n\nB内容\n" * 10_000).encode())
    harness.ingest(project_a, source_a.source_id)
    harness.ingest(project_b, source_b.source_id)

    draft_a = harness.incubate(project_a, source_a.source_id)
    draft_b = harness.incubate(project_b, source_b.source_id)
    baseline_a = harness.publish(project_a, draft_a.id)
    baseline_b = harness.publish(project_b, draft_b.id)

    assert harness.export(project_a).content != harness.export(project_b).content
    assert baseline_a.project_id == "PROJECT_A"
    assert baseline_b.project_id == "PROJECT_B"
    assert project_a.manifest_path.read_bytes() != project_b.manifest_path.read_bytes()
