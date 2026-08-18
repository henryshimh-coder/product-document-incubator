from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from src.application.dto.documents import IncubateDocumentInput, IncubationView
from src.application.dto.materials import CreateLocalDraftInput
from src.application.ports.incubator import DocumentDraftRepository
from src.application.ports.repositories import ProjectRepository, SourceRepository
from src.application.ports.wiki_ingest import WikiContextReading
from src.domain.enums import CallResultMode, DocumentDraftStatus, SecurityLevel
from src.domain.errors import DomainError, ErrorCode
from src.domain.incubator import DocumentDraft, DocumentSectionCitation
from src.domain.models import ModelCallLog, Project, SourceRecord
from src.domain.policies.security_policy import can_call_external_model
from src.infrastructure.files.document_store import DocumentStore
from src.infrastructure.files.markdown_sections import extract_headings, validate_product_markdown
from src.infrastructure.files.project_library import ProjectPaths
from src.infrastructure.files.redactor import redact_text
from src.infrastructure.observability.model_call_logger import ModelCallLogger


class DocumentWorkflow(Protocol):
    def generate_draft(self, inputs: Mapping[str, Any]) -> dict[str, Any]: ...


class AcceptedSuggestionReader(Protocol):
    def accepted_titles(self, project_id: str) -> list[str]: ...


class LocalDocumentDraftCreator(Protocol):
    def execute(self, command: CreateLocalDraftInput) -> IncubationView: ...


class VersionIdFactory:
    @staticmethod
    def next(project_id: str, now: datetime, existing_ids: Iterable[str]) -> str:
        prefix = f"{project_id}-{now:%Y%m%d}-"
        numbers = [
            int(value.removeprefix(prefix))
            for value in existing_ids
            if value.startswith(prefix) and value.removeprefix(prefix).isdigit()
        ]
        return f"{prefix}{max(numbers, default=0) + 1:02d}"


class IncubateDocument:
    """Create an immutable candidate product document without touching current."""

    def __init__(
        self,
        *,
        paths: ProjectPaths,
        projects: ProjectRepository,
        sources: SourceRepository,
        drafts: DocumentDraftRepository,
        store: DocumentStore,
        gateway: DocumentWorkflow | None,
        wiki_context: WikiContextReading,
        model_call_logger: ModelCallLogger,
        local_draft_creator: LocalDocumentDraftCreator | None = None,
        accepted_suggestions: AcceptedSuggestionReader | None = None,
        customer_names: Iterable[str] = (),
        strategy_terms: Iterable[str] = (),
        financial_terms: Iterable[str] = (),
        leader_names: Iterable[str] = (),
        unpublished_decisions: Iterable[str] = (),
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.paths = paths
        self.projects = projects
        self.sources = sources
        self.drafts = drafts
        self.store = store
        self.gateway = gateway
        self.wiki_context = wiki_context
        self.model_call_logger = model_call_logger
        self.local_draft_creator = local_draft_creator
        self.accepted_suggestions = accepted_suggestions
        self.customer_names = tuple(customer_names)
        self.strategy_terms = tuple(strategy_terms)
        self.financial_terms = tuple(financial_terms)
        self.leader_names = tuple(leader_names)
        self.unpublished_decisions = tuple(unpublished_decisions)
        self.now = now or (lambda: datetime.now(UTC))

    def list_sources(self, project_id: str) -> list[dict[str, str]]:
        if project_id != self.paths.project_id:
            raise ValueError("incubation project_id does not match active project")
        return [
            view.model_dump(mode="json")
            for view in self.wiki_context.list_ingested_sources(project_id)
        ]

    def list_drafts(self, project_id: str) -> list[DocumentDraft]:
        if project_id != self.paths.project_id:
            raise ValueError("incubation project_id does not match active project")
        return self.drafts.list_for_project(project_id)

    def save_draft(self, project_id: str, draft_id: str, markdown: str) -> DocumentDraft:
        if project_id != self.paths.project_id:
            raise ValueError("incubation project_id does not match active project")
        draft = self.drafts.get(draft_id)
        if draft.project_id != project_id:
            raise ValueError("document draft project_id mismatch")
        normalized = validate_product_markdown(markdown)
        digest = self.store.replace_draft(draft.markdown_path, normalized)
        updated = draft.model_copy(
            update={
                "status": DocumentDraftStatus.PENDING_OWNER,
                "markdown_sha256": digest,
                "updated_at": self.now(),
            }
        )
        self.drafts.update(updated)
        self.store.append_log(
            f"- {updated.updated_at.isoformat()} Owner 保存候选文档 {draft.version_id}。\n"
        )
        return updated

    def execute(self, command: IncubateDocumentInput) -> IncubationView:
        if command.project_id != self.paths.project_id:
            raise ValueError("incubation project_id does not match active project")
        project = self.projects.get(command.project_id)
        sources = self._sources_for_command(project, command.source_ids)
        if any(source.security_level.value in {"L3", "L4"} for source in sources):
            if len(sources) != 1 or self.local_draft_creator is None:
                raise DomainError(ErrorCode.EXTERNAL_CALL_DENIED, "DOCUMENT_LOCAL_ROUTE_REQUIRED")
            return self.local_draft_creator.execute(
                CreateLocalDraftInput(
                    project_id=command.project_id,
                    source_id=sources[0].id,
                    requested_by=command.requested_by,
                )
            )
        context = self.wiki_context.read_context(command.project_id, command.source_ids)
        now = self.now()
        existing = self.drafts.list_for_project(command.project_id)
        version_id = VersionIdFactory.next(
            command.project_id, now, (item.version_id for item in existing)
        )
        baseline = self.store.read_current()
        parent_version_id = self._current_version() if baseline is not None else None
        inputs = {
            "schema_version": "2.0",
            "task_type": "document_draft",
            "project_id": command.project_id,
            "project_name": project.name,
            "project_description": project.product_line,
            "schema_headings": self._schema_headings(command.project_id),
            "current_document_markdown": self._safe_current(baseline),
            "wiki_pages": [
                page.model_dump(mode="json")
                for page in context.pages
                if page.safe_for_external
            ],
        }
        outbound_chars = len(
            json.dumps(inputs, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )
        source_total_chars = sum(len(item["excerpt"]) for item in inputs["wiki_pages"])
        started = self.now()
        try:
            if self.gateway is None:
                raise ValueError("DOCUMENT_GATEWAY_NOT_CONFIGURED")
            response = self.gateway.generate_draft(inputs)
            result = response["result"]
            markdown = validate_product_markdown(str(result["document_markdown"]))
            section_citations = [
                DocumentSectionCitation.model_validate(item) for item in result["section_citations"]
            ]
            self._validate_citation_headings(markdown, section_citations)
            markdown_path, markdown_sha256 = self.store.write_draft(version_id, markdown)
        except BaseException as error:
            self._record_call(
                project_id=command.project_id,
                source_ids=list(command.source_ids),
                version_id=parent_version_id or "INITIAL",
                started=started,
                status="failed",
                workflow_run_id=None,
                error_code=getattr(error, "code", "DOCUMENT_INCUBATION_FAILED"),
                outbound_chars=outbound_chars,
                # 文档工作流的固定 Schema 与基线会使请求总字符大于材料
                # 摘录本身；日志以 1.0 表示当前选择材料已被完全覆盖。
                outbound_coverage=min(1.0, outbound_chars / max(source_total_chars, 1)),
            )
            raise
        draft = DocumentDraft(
            id=f"DRAFT-{uuid4().hex.upper()}",
            project_id=command.project_id,
            version_id=version_id,
            parent_version_id=parent_version_id,
            status=DocumentDraftStatus.CANDIDATE_DRAFT,
            markdown_path=markdown_path,
            markdown_sha256=markdown_sha256,
            source_ids=list(command.source_ids),
            section_citations=section_citations,
            summary=result["summary"],
            missing_sections=result["missing_sections"],
            evidence_gaps=result["evidence_gaps"],
            created_at=now,
            updated_at=now,
        )
        try:
            self.drafts.add(draft)
        except BaseException:
            (self.paths.project_root / markdown_path).unlink(missing_ok=True)
            raise
        self.store.append_log(f"- {now.isoformat()} 生成候选产品文档 {version_id}。\n")
        self._record_call(
            project_id=command.project_id,
            source_ids=list(command.source_ids),
            version_id=parent_version_id or "INITIAL",
            started=started,
            status="succeeded",
            workflow_run_id=str(response["workflow_run_id"]),
            error_code=None,
            outbound_chars=outbound_chars,
            outbound_coverage=min(1.0, outbound_chars / max(source_total_chars, 1)),
        )
        return IncubationView(draft=draft, markdown=markdown)

    def _sources_for_command(self, project: Project, source_ids: list[str]) -> list[SourceRecord]:
        if len(set(source_ids)) != len(source_ids):
            raise ValueError("source_ids must be unique")
        selected = [self.sources.get(source_id) for source_id in source_ids]
        for source in selected:
            if source.ingest_status != "ingested":
                raise ValueError("DOCUMENT_SOURCE_NOT_INGESTED")
            if source.project_id != project.id:
                raise DomainError(
                    ErrorCode.EXTERNAL_CALL_DENIED, "DOCUMENT_SOURCE_PROJECT_MISMATCH"
                )
            if source.security_level in {
                SecurityLevel.L3_CONFIDENTIAL,
                SecurityLevel.L4_RESTRICTED,
            }:
                continue
            if not can_call_external_model(project, source):
                raise DomainError(ErrorCode.EXTERNAL_CALL_DENIED, "DOCUMENT_SOURCE_NOT_AUTHORIZED")
        return selected

    def _schema_headings(self, project_id: str) -> list[str]:
        template = self.paths.schema_root / "product-document-template.md"
        headings = extract_headings(template.read_text(encoding="utf-8"))
        if not headings:
            raise ValueError("DOCUMENT_SCHEMA_HEADINGS_EMPTY")
        if self.accepted_suggestions is not None:
            for title in self.accepted_suggestions.accepted_titles(project_id):
                if title not in headings:
                    headings.append(title)
        return headings

    def _safe_current(self, baseline: str | None) -> str | None:
        if baseline is None:
            return None
        redaction = redact_text(
            baseline,
            customer_names=self.customer_names,
            strategy_terms=self.strategy_terms,
            financial_terms=self.financial_terms,
            leader_names=self.leader_names,
            unpublished_decisions=self.unpublished_decisions,
        )
        if not redaction.safe_for_external_model:
            raise DomainError(ErrorCode.REDACTION_REQUIRED, "CURRENT_DOCUMENT_REDACTION_REQUIRED")
        return redaction.redacted_text

    def _current_version(self) -> str:
        if not self.paths.manifest_path.is_file():
            raise ValueError("CURRENT_VERSION_MISSING")
        payload = json.loads(self.paths.manifest_path.read_text(encoding="utf-8"))
        version = payload.get("current_version")
        if not isinstance(version, str) or not version.strip():
            raise ValueError("CURRENT_VERSION_MISSING")
        return version

    @staticmethod
    def _validate_citation_headings(
        markdown: str, citations: list[DocumentSectionCitation]
    ) -> None:
        valid = set(extract_headings(markdown))
        if any(citation.heading not in valid for citation in citations):
            raise ValueError("DOCUMENT_CITATION_HEADING_INVALID")

    def _record_call(
        self,
        *,
        project_id: str,
        source_ids: list[str],
        version_id: str,
        started: datetime,
        status: str,
        workflow_run_id: str | None,
        error_code: str | None,
        outbound_chars: int,
        outbound_coverage: float,
    ) -> None:
        finished = self.now()
        self.model_call_logger.record(
            ModelCallLog(
                id=f"MODEL-DOCUMENT-{uuid4().hex.upper()}",
                project_id=project_id,
                task_type="document_draft",
                workflow_run_id=workflow_run_id,
                correlation_id=f"DOCUMENT-{uuid4().hex.upper()}",
                source_ids=source_ids,
                baseline_version=version_id,
                model_label="dify-document",
                prompt_version="document-v2",
                schema_version="2.0",
                authorized=True,
                redacted=True,
                outbound_chars=outbound_chars,
                outbound_coverage=outbound_coverage,
                result_mode=CallResultMode.REALTIME,
                status=status,
                started_at=started,
                finished_at=finished,
                elapsed_ms=max(0, int((finished - started).total_seconds() * 1000)),
                error_code=error_code,
            )
        )
