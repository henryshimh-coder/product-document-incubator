from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.application.dto.documents import IncubateDocumentInput
from src.domain.enums import AuthorityLevel, SecurityLevel
from src.domain.errors import DomainError
from src.domain.models import Project, SourceRecord
from src.infrastructure.db.connection import connect
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
        self.call_count = 0

    def generate_draft(self, inputs: dict, *, on_started=None) -> dict:
        self.call_count += 1
        self.inputs = inputs
        if on_started is not None:
            on_started("TASK-DOCUMENT-001", "WF-DOCUMENT-001")
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
    (paths.system_root / "project.json").write_text(
        json.dumps(
            {
                "schema_version": "2.2",
                "project_id": "NEW",
                "allow_external_model": True,
            }
        ),
        encoding="utf-8",
    )
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


def _add_source(service, source_id: str, *, security_level: SecurityLevel) -> SourceRecord:
    template = service.sources.get("SRC-001")
    source = template.model_copy(
        update={
            "id": source_id,
            "sha256": ("b" if security_level == SecurityLevel.L2_INTERNAL else "c") * 64,
            "archive_path": f"raw/2026/{source_id}/source.md",
            "security_level": security_level,
            "source_page_path": None,
            "topic_page_paths": [],
            "allow_external_model": security_level == SecurityLevel.L2_INTERNAL,
        }
    )
    service.sources.add(source)
    return source


def _attach_topic(
    paths: ProjectPaths,
    service,
    *,
    body: str,
    project_id: str = "NEW",
) -> str:
    path = paths.wiki_root / "topics/product-principles.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\npage_type: topic\ntopic_id: product-principles\nproject_id: "
        + project_id
        + "\n---\n# 主题：产品原则\n\n## 当前综合结论\n\n- "
        + body
        + "\n",
        encoding="utf-8",
    )
    relative = path.relative_to(paths.project_root).as_posix()
    source = service.sources.get("SRC-001")
    service.sources.update(source.model_copy(update={"topic_page_paths": [relative]}))
    return relative


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


def test_incubation_reports_dify_identifiers_when_stream_starts(tmp_path: Path) -> None:
    _, service, _ = _environment(tmp_path)
    started: list[tuple[str, str]] = []

    service.execute(
        _command(),
        on_started=lambda task_id, workflow_run_id: started.append((task_id, workflow_run_id)),
    )

    assert started == [("TASK-DOCUMENT-001", "WF-DOCUMENT-001")]


def test_completed_workflow_can_be_persisted_without_second_gateway_call(
    tmp_path: Path,
) -> None:
    paths, service, gateway = _environment(tmp_path)
    page = service.wiki_context.read_context("NEW", ["SRC-001"]).pages[0]
    response = {
        "workflow_run_id": "WF-RECOVERED-001",
        "status": "succeeded",
        "result": {
            "document_markdown": "# 新产品方案\n\n## 产品概述\n\n恢复已完成的候选文档。",
            "summary": "已恢复候选文档。",
            "missing_sections": [],
            "evidence_gaps": [],
            "source_ids": [page.source_id],
            "section_citations": [
                {
                    "heading": "产品概述",
                    "source_id": page.source_id,
                    "chunk_id": page.chunk_id,
                    "locator": page.locator,
                    "excerpt": page.excerpt,
                }
            ],
        },
    }

    result = service.complete_from_workflow(_command(), response)

    assert gateway.call_count == 0
    assert result.draft.version_id == "NEW-20260812-01"
    assert (paths.project_root / result.draft.markdown_path).is_file()
    assert service.drafts.list_for_project("NEW") == [result.draft]


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
    with connect(service.model_call_logger.db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM model_call_logs").fetchone()[0] == 0


@pytest.mark.parametrize(
    "topic_body",
    [
        "安全结论【SRC-001：section】 SECRET-COLON 【SRC:L3:section】",
        "安全结论【SRC-001：section】 SECRET-CROSS-LINE 【SRC:L3\n：section】",
        "安全结论【SRC-001：section】 SECRET-MALFORMED 【SRC.L3：section】",
    ],
)
def test_incubation_excludes_entire_unsafe_topic_from_external_wiki_context(
    tmp_path: Path,
    topic_body: str,
) -> None:
    paths, service, gateway = _environment(tmp_path)
    _add_source(service, "SRC:L3", security_level=SecurityLevel.L3_CONFIDENTIAL)
    _attach_topic(paths, service, body=topic_body)

    service.execute(_command())

    assert gateway.inputs is not None
    serialized = json.dumps(gateway.inputs, ensure_ascii=False)
    assert "SECRET-" not in serialized
    assert "wiki/topics/product-principles.md" not in serialized


def test_owner_edited_source_page_with_sensitive_citation_never_reaches_gateway(
    tmp_path: Path,
) -> None:
    """A source page is atomic outbound context: one unsafe citation blocks all of it."""
    paths, service, gateway = _environment(tmp_path)
    restricted = _add_source(service, "SRC-L3", security_level=SecurityLevel.L3_CONFIDENTIAL)
    source_page = paths.wiki_root / "sources/SRC-001-需求.md"
    source_page.write_text(
        source_page.read_text(encoding="utf-8") + "\n泄露内容【SRC-L3：secret section】\n",
        encoding="utf-8",
    )

    with pytest.raises(DomainError, match="EXTERNAL_CALL_DENIED"):
        service.execute(_command())

    assert gateway.inputs is None
    assert restricted.security_level is SecurityLevel.L3_CONFIDENTIAL


@pytest.mark.parametrize(
    "project_id,topic_body,error_detail",
    [
        ("OTHER", "安全结论【SRC-001：section】", "WIKI_TOPIC_PROJECT_MISMATCH"),
        ("NEW", "其他结论【SRC-OTHER：section】", "WIKI_TOPIC_SOURCE_MISMATCH"),
    ],
)
def test_incubation_rejects_topic_with_project_or_source_ownership_mismatch(
    tmp_path: Path,
    project_id: str,
    topic_body: str,
    error_detail: str,
) -> None:
    paths, service, gateway = _environment(tmp_path)
    if "SRC-OTHER" in topic_body:
        _add_source(service, "SRC-OTHER", security_level=SecurityLevel.L2_INTERNAL)
    _attach_topic(paths, service, body=topic_body, project_id=project_id)

    with pytest.raises(DomainError, match=error_detail):
        service.execute(_command())

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
