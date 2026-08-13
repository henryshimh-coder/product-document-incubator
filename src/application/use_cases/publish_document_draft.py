from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import UTC, datetime

from src.application.dto.documents import PublishDocumentDraftInput
from src.application.ports.incubator import DocumentDraftRepository
from src.application.ports.repositories import ProjectRepository, SourceRepository
from src.domain.enums import BaselineStatus, DocumentDraftStatus, KnowledgeStatus
from src.domain.errors import DomainError, ErrorCode
from src.domain.models import Baseline, KnowledgeCard
from src.infrastructure.files.document_store import DocumentStore
from src.infrastructure.files.manifest_store import ManifestStore
from src.infrastructure.files.markdown_sections import validate_product_markdown
from src.infrastructure.files.project_library import ProjectPaths
from src.infrastructure.recovery.reconciliation_service import ReconciliationService


class PublishDocumentDraft:
    """Owner-gated product-document publication with a manifest switch point."""

    def __init__(
        self,
        *,
        paths: ProjectPaths,
        projects: ProjectRepository,
        sources: SourceRepository,
        drafts: DocumentDraftRepository,
        store: DocumentStore,
        manifest: ManifestStore,
        reconciliation: ReconciliationService,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.paths = paths
        self.projects = projects
        self.sources = sources
        self.drafts = drafts
        self.store = store
        self.manifest = manifest
        self.reconciliation = reconciliation
        self.now = now or (lambda: datetime.now(UTC))

    def execute(self, command: PublishDocumentDraftInput) -> Baseline:
        if command.project_id != self.paths.project_id:
            raise DomainError(ErrorCode.RELEASE_PROJECT_MISMATCH)
        project = self.projects.get(command.project_id)
        draft = self.drafts.get(command.draft_id)
        if draft.project_id != project.id or draft.status != DocumentDraftStatus.PENDING_OWNER:
            raise DomainError(ErrorCode.CHANGE_NOT_APPROVED, "DOCUMENT_DRAFT_NOT_PENDING_OWNER")
        markdown = (self.paths.project_root / draft.markdown_path).read_text(encoding="utf-8")
        if hashlib.sha256(markdown.encode("utf-8")).hexdigest() != draft.markdown_sha256:
            raise DomainError(ErrorCode.BASELINE_INTEGRITY_FAILED, "DRAFT_HASH_MISMATCH")
        markdown = validate_product_markdown(markdown)
        current = self._read_current()
        if current is None:
            if draft.parent_version_id is not None:
                raise DomainError(ErrorCode.RELEASE_FAILED, "INITIAL_DRAFT_PARENT_MISMATCH")
        elif draft.parent_version_id != current.current_version:
            raise DomainError(ErrorCode.RELEASE_FAILED, "DRAFT_PARENT_MISMATCH")
        cards = self._compile_cards(project.id, draft.version_id, markdown, draft)
        now = self.now()
        manifest_replaced = False
        try:
            full_path, full_sha, cards_path, cards_sha = self.store.commit_version(
                version_id=draft.version_id,
                markdown=markdown,
                cards=[item.model_dump(mode="json") for item in cards],
            )
            candidate = self.manifest.build_initial_candidate(
                project_id=project.id,
                version=draft.version_id,
                display_version=command.display_version,
                approved_by=command.owner_name,
                published_at=now,
                full_document_path=full_path,
                card_snapshot_path=cards_path,
                full_document_sha256=full_sha,
                card_snapshot_sha256=cards_sha,
            )
            if current is not None:
                candidate = candidate.model_copy(
                    update={
                        "parent_baseline_id": current.current_baseline_id,
                    }
                )
            self.manifest.atomic_replace(candidate)
            manifest_replaced = True
            self.store.sync_current_from_version(full_path)
        except OSError as error:
            if not manifest_replaced:
                self.store.discard_version(draft.version_id)
                raise DomainError(ErrorCode.RELEASE_FAILED, "DOCUMENT_PUBLISH_IO_FAILED") from error
            repair = self.reconciliation.rebuild_current_from_manifest()
            if not repair.success:
                raise DomainError(
                    ErrorCode.RELEASE_MIRROR_REPAIR_REQUIRED,
                    repair.error_code or "DOCUMENT_MIRROR_REPAIR_FAILED",
                ) from error
            raise DomainError(ErrorCode.RELEASE_FAILED, "DOCUMENT_MIRROR_REPAIRED") from error
        repair = self.reconciliation.rebuild_current_from_manifest()
        if not repair.success:
            raise DomainError(
                ErrorCode.RELEASE_MIRROR_REPAIR_REQUIRED,
                repair.error_code or "DOCUMENT_MIRROR_REPAIR_FAILED",
            )
        new_snapshot = self.manifest.read_snapshot()
        baseline = Baseline(
            id=candidate.current_baseline_id,
            project_id=project.id,
            version=draft.version_id,
            parent_baseline_id=candidate.parent_baseline_id,
            status=BaselineStatus.EFFECTIVE,
            full_document_path=full_path,
            card_snapshot_path=cards_path,
            manifest_sha256=new_snapshot.sha256,
            full_document_sha256=full_sha,
            card_snapshot_sha256=cards_sha,
            change_request_id=None,
            approved_by=command.owner_name,
            effective_at=now,
            created_at=now,
            display_version=command.display_version,
        )
        self.drafts.update(
            draft.model_copy(update={"status": DocumentDraftStatus.PUBLISHED, "updated_at": now})
        )
        self.store.append_log(f"- {now.isoformat()} Owner 发布产品文档 {draft.version_id}。\n")
        return baseline

    def _read_current(self):
        if not self.paths.manifest_path.is_file():
            return None
        try:
            return self.manifest.read_and_validate()
        except ValueError as error:
            raise DomainError(ErrorCode.BASELINE_INTEGRITY_FAILED, "MANIFEST_INVALID") from error

    def _compile_cards(
        self, project_id: str, version_id: str, markdown: str, draft
    ) -> list[KnowledgeCard]:
        h2_headings = [line[3:].strip() for line in markdown.splitlines() if line.startswith("## ")]
        citations_by_heading = {item.heading: item for item in draft.section_citations}
        if not h2_headings or any(heading not in citations_by_heading for heading in h2_headings):
            raise DomainError(
                ErrorCode.PUBLISH_CITATION_UNVERIFIABLE, "DOCUMENT_SECTION_CITATION_REQUIRED"
            )
        now = draft.updated_at
        cards: list[KnowledgeCard] = []
        for heading in h2_headings:
            body = _section_body(markdown, heading)
            citation = citations_by_heading[heading]
            if citation.source_id not in draft.source_ids:
                raise DomainError(
                    ErrorCode.PUBLISH_CITATION_UNVERIFIABLE,
                    "DOCUMENT_CITATION_SOURCE_NOT_SELECTED",
                )
            try:
                source = self.sources.get(citation.source_id)
            except KeyError as error:
                raise DomainError(
                    ErrorCode.PUBLISH_CITATION_UNVERIFIABLE,
                    "DOCUMENT_CITATION_SOURCE_MISSING",
                ) from error
            if source.project_id != project_id:
                raise DomainError(
                    ErrorCode.PUBLISH_CITATION_UNVERIFIABLE,
                    "DOCUMENT_CITATION_SOURCE_PROJECT_MISMATCH",
                )
            cards.append(
                KnowledgeCard(
                    id=f"{project_id}-SECTION-{hashlib.sha256(heading.encode()).hexdigest()[:12]}",
                    project_id=project_id,
                    card_type="product_section",
                    title=heading,
                    content=body,
                    status=KnowledgeStatus.EFFECTIVE,
                    product_version=version_id,
                    applicable_scope="产品方案",
                    source_refs=[f"{citation.source_id}:{citation.chunk_id}"],
                    authority_level="formal_decision",
                    owner="产品经理",
                    created_at=now,
                    updated_at=now,
                )
            )
        return cards


def _section_body(markdown: str, heading: str) -> str:
    marker = f"## {heading}"
    tail = markdown.split(marker, 1)[1]
    return tail.split("\n## ", 1)[0].strip()
