from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from src.application.dto.documents import IncubationView
from src.application.dto.materials import CreateLocalDraftInput
from src.domain.enums import DocumentDraftStatus, DocumentGenerationMode, SecurityLevel
from src.domain.incubator import DocumentDraft, DocumentSectionCitation
from src.infrastructure.files.markdown_sections import extract_headings


class CreateLocalDocumentDraft:
    """Pure-local draft creator with no gateway, cache, or logger dependency."""

    def __init__(self, *, paths, projects, sources, drafts, store, now=None) -> None:
        self.paths = paths
        self.projects = projects
        self.sources = sources
        self.drafts = drafts
        self.store = store
        self.now = now or (lambda: datetime.now(UTC))

    def execute(self, command: CreateLocalDraftInput):
        if command.project_id != self.paths.project_id:
            raise ValueError("LOCAL_DRAFT_PROJECT_MISMATCH")
        project = self.projects.get(command.project_id)
        source = self.sources.get(command.source_id)
        if source.project_id != project.id:
            raise ValueError("LOCAL_DRAFT_PROJECT_MISMATCH")
        if source.security_level not in (
            SecurityLevel.L3_CONFIDENTIAL,
            SecurityLevel.L4_RESTRICTED,
        ):
            raise ValueError("LOCAL_DRAFT_SENSITIVE_SOURCE_REQUIRED")
        now = self.now()
        markdown = self.store.read_current()
        if markdown is None:
            template = self.paths.schema_root / "product-document-template.md"
            markdown = template.read_text(encoding="utf-8").replace("{产品名称}", project.name)
        version_id = f"{project.id}-{now:%Y%m%d}-LOCAL-{uuid4().hex[:8].upper()}"
        markdown_path, markdown_sha256 = self.store.write_draft(version_id, markdown)
        citations = [
            DocumentSectionCitation(
                heading=heading,
                source_id=source.id,
                chunk_id="LOCAL-MANUAL",
                locator="owner-local-review",
                excerpt="本地人工复核",
            )
            for heading in extract_headings(markdown)
        ]
        draft = DocumentDraft(
            id=f"DRAFT-{uuid4().hex.upper()}",
            project_id=project.id,
            version_id=version_id,
            parent_version_id=None,
            status=DocumentDraftStatus.CANDIDATE_DRAFT,
            markdown_path=markdown_path,
            markdown_sha256=markdown_sha256,
            source_ids=[source.id],
            section_citations=citations,
            summary="本地人工候选",
            missing_sections=[],
            evidence_gaps=[],
            created_at=now,
            updated_at=now,
            generation_mode=DocumentGenerationMode.LOCAL_MANUAL,
        )
        try:
            self.drafts.add(draft)
        except BaseException:
            (self.paths.project_root / markdown_path).unlink(missing_ok=True)
            raise
        self.store.append_log(f"- {now.isoformat()} 创建本地候选产品文档 {version_id}。\n")
        return IncubationView(draft=draft, markdown=markdown)
