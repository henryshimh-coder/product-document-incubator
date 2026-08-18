from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from src.application.dto.documents import IncubateDocumentInput
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
from src.infrastructure.files.wiki_context_reader import WikiContextReader
from src.infrastructure.observability.model_call_logger import ModelCallLogger

NOW = datetime(2026, 8, 12, 10, 0, tzinfo=UTC)


class FakeDocumentGateway:
    def __init__(self) -> None:
        self.inputs: dict | None = None

    def generate_draft(self, inputs: dict) -> dict:
        self.inputs = inputs
        page = inputs["wiki_pages"][0]
        assert page["source_id"] == "SRC-001"
        return {
            "workflow_run_id": "WF-DOCUMENT-001",
            "result": {
                "document_markdown": "# 新产品方案\n\n## 产品概述\n\n支持 Owner 管理独立项目。",
                "summary": "已生成首版候选。",
                "missing_sections": ["验收标准"],
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


def _environment(tmp_path: Path, *, with_current: bool = False):
    from src.application.use_cases.incubate_document import IncubateDocument

    library = tmp_path / "library"
    paths = ProjectPaths.for_project(library, "NEW")
    paths.raw_root.mkdir(parents=True)
    paths.wiki_root.mkdir(parents=True)
    paths.schema_root.mkdir(parents=True)
    paths.system_root.mkdir(parents=True)
    (paths.wiki_root / "log.md").write_text("# 项目日志\n", encoding="utf-8")
    (paths.schema_root / "product-document-template.md").write_text(
        "# 新产品方案\n\n## 产品概述\n\n## 验收标准\n",
        encoding="utf-8",
    )
    if with_current:
        current = paths.wiki_root / "current" / "当前产品方案.md"
        current.parent.mkdir(parents=True)
        current.write_text("# 当前产品方案\n\n## 产品概述\n\n原始内容。", encoding="utf-8")
        paths.manifest_path.write_text(
            json.dumps({"current_version": "NEW-20260801-01"}), encoding="utf-8"
        )
    db_path = library / ".incubator/product_incubator.db"
    migrate(db_path)
    projects = SqliteProjectRepository(db_path)
    projects.add(
        Project(
            id="NEW",
            name="新产品",
            product_line="产品文档孵化",
            stage="待初始化",
            current_baseline_id=None,
            allow_external_model=True,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    source_path = paths.raw_root / "2026" / "SRC-001" / "需求.md"
    source_path.parent.mkdir(parents=True)
    source_path.write_text("# 产品目标\n\nOwner 可以建立独立产品项目。", encoding="utf-8")
    payload = source_path.read_bytes()
    SqliteSourceRepository(db_path).add(
        SourceRecord(
            id="SRC-001",
            project_id="NEW",
            original_filename="需求.md",
            archive_path=str(source_path),
            sha256=hashlib.sha256(payload).hexdigest(),
            mime_type="text/plain",
            size_bytes=len(payload),
            source_type="product_requirement",
            authority_level=AuthorityLevel.FORMAL_DECISION,
            source_department="产品部",
            provider=None,
            document_date=NOW.date(),
            document_version="v1.0",
            applicable_baseline_version="未关联基线",
            security_level=SecurityLevel.L2_INTERNAL,
            is_redacted=True,
            allow_external_model=True,
            is_sandbox=False,
            ingest_status="ingested",
            source_page_path="wiki/sources/SRC-001-需求.md",
            created_at=NOW,
        )
    )
    source_page = paths.wiki_root / "sources/SRC-001-需求.md"
    source_page.parent.mkdir(parents=True)
    source_page.write_text(
        "---\nproject_id: NEW\nsource_id: SRC-001\nraw_sha256: "
        + hashlib.sha256(payload).hexdigest()
        + "\n---\n# 来源：需求\n\n产品文档孵化内容。\n\n"
        "来源定位【SRC-001：heading:产品目标; line:1】",
        encoding="utf-8",
    )
    gateway = FakeDocumentGateway()
    service = IncubateDocument(
        paths=paths,
        projects=projects,
        sources=SqliteSourceRepository(db_path),
        drafts=SqliteDocumentDraftRepository(db_path),
        store=DocumentStore(paths),
        gateway=gateway,
        wiki_context=WikiContextReader(
            paths=paths,
            sources=SqliteSourceRepository(db_path),
        ),
        model_call_logger=ModelCallLogger(db_path),
        now=lambda: NOW,
    )
    return paths, service, gateway


def _command() -> IncubateDocumentInput:
    return IncubateDocumentInput(project_id="NEW", source_ids=["SRC-001"], requested_by="Owner")


def test_initial_incubation_creates_draft_without_current_baseline(tmp_path: Path) -> None:
    paths, service, gateway = _environment(tmp_path)
    (paths.raw_root / "2026" / "SRC-001" / "需求.md").unlink()

    result = service.execute(_command())

    assert result.draft.parent_version_id is None
    assert result.draft.status.value == "candidate_draft"
    assert (
        (paths.project_root / result.draft.markdown_path)
        .read_text(encoding="utf-8")
        .startswith("# ")
    )
    assert not (paths.wiki_root / "current" / "当前产品方案.md").exists()
    assert gateway.inputs is not None
    assert gateway.inputs["current_document_markdown"] is None
    assert gateway.inputs["wiki_pages"][0]["page_path"] == "wiki/sources/SRC-001-需求.md"


def test_incubation_lists_only_ingested_sources(tmp_path: Path) -> None:
    _, service, _ = _environment(tmp_path)

    assert service.list_sources("NEW") == [
        {
            "id": "SRC-001",
            "label": "需求.md · SRC-001",
            "wiki_page_count": 1,
            "conflict_count": 0,
            "evidence_gap_count": 0,
        }
    ]


def test_incubation_excludes_pending_sources(tmp_path: Path) -> None:
    _, service, _ = _environment(tmp_path)
    source = service.sources.get("SRC-001")
    service.sources.update(source.model_copy(update={"ingest_status": "pending_ingest"}))

    assert service.list_sources("NEW") == []


def test_sensitive_wiki_uses_local_candidate_without_document_gateway(tmp_path: Path) -> None:
    from src.application.use_cases.create_local_document_draft import CreateLocalDocumentDraft

    paths, service, gateway = _environment(tmp_path)
    source = service.sources.get("SRC-001")
    service.sources.update(
        source.model_copy(
            update={
                "security_level": SecurityLevel.L3_CONFIDENTIAL,
                "allow_external_model": False,
            }
        )
    )
    service.local_draft_creator = CreateLocalDocumentDraft(
        paths=paths,
        projects=service.projects,
        sources=service.sources,
        drafts=service.drafts,
        store=service.store,
        now=lambda: NOW,
    )

    result = service.execute(_command())

    assert result.draft.generation_mode.value == "local_manual"
    assert gateway.inputs is None


def test_incremental_incubation_reads_current_and_never_overwrites_it(tmp_path: Path) -> None:
    paths, service, gateway = _environment(tmp_path, with_current=True)
    current = paths.wiki_root / "current" / "当前产品方案.md"
    before = current.read_bytes()

    result = service.execute(_command())

    assert result.draft.parent_version_id == "NEW-20260801-01"
    assert current.read_bytes() == before
    assert gateway.inputs is not None
    assert gateway.inputs["current_document_markdown"] == before.decode("utf-8")


def test_incubation_includes_accepted_structure_suggestions_in_schema(tmp_path: Path) -> None:
    paths, service, gateway = _environment(tmp_path)

    class AcceptedSuggestions:
        def accepted_titles(self, project_id: str) -> list[str]:
            assert project_id == "NEW"
            return ["风险边界"]

    service.accepted_suggestions = AcceptedSuggestions()
    service.execute(_command())

    assert gateway.inputs is not None
    assert gateway.inputs["schema_headings"] == ["新产品方案", "产品概述", "验收标准", "风险边界"]
