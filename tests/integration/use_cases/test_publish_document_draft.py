from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.domain.enums import AuthorityLevel, DocumentDraftStatus, SecurityLevel
from src.domain.incubator import DocumentDraft, DocumentSectionCitation
from src.domain.models import Project, SourceRecord
from src.infrastructure.db.migrations import migrate
from src.infrastructure.db.repositories import (
    SqliteDocumentDraftRepository,
    SqliteProjectRepository,
    SqliteSourceRepository,
)
from src.infrastructure.files.document_store import DocumentStore
from src.infrastructure.files.manifest_store import ManifestStore
from src.infrastructure.files.project_library import ProjectPaths
from src.infrastructure.recovery.reconciliation_service import ReconciliationService

NOW = datetime(2026, 8, 12, 11, 0, tzinfo=UTC)
MARKDOWN = "# 新产品方案\n\n## 产品概述\n\n支持 Owner 创建并管理独立项目。"


class PublishEnvironment:
    def __init__(self, tmp_path: Path, *, with_current: bool = False) -> None:
        from src.application.use_cases.publish_document_draft import PublishDocumentDraft

        library = tmp_path / "library"
        self.paths = ProjectPaths.for_project(library, "NEW")
        self.paths.wiki_root.mkdir(parents=True)
        self.paths.system_root.mkdir(parents=True)
        (self.paths.wiki_root / "log.md").write_text("# 项目日志\n", encoding="utf-8")
        db_path = library / ".incubator/product_incubator.db"
        migrate(db_path)
        self.projects = SqliteProjectRepository(db_path)
        self.projects.add(
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
        SqliteSourceRepository(db_path).add(
            SourceRecord(
                id="SRC-001",
                project_id="NEW",
                original_filename="需求.md",
                archive_path="raw/2026/SRC-001/需求.md",
                sha256="a" * 64,
                mime_type="text/plain",
                size_bytes=1,
                source_type="product_requirement",
                authority_level=AuthorityLevel.FORMAL_DECISION,
                source_department="产品部",
                provider=None,
                document_date=NOW.date(),
                document_version="1.0",
                applicable_baseline_version="未关联基线",
                security_level=SecurityLevel.L2_INTERNAL,
                is_redacted=True,
                allow_external_model=True,
                is_sandbox=False,
                ingest_status="archived",
                created_at=NOW,
            )
        )
        self.store = DocumentStore(self.paths)
        self.current_path = self.paths.wiki_root / "current" / "当前产品方案.md"
        if with_current:
            self.current_path.parent.mkdir(parents=True)
            self.current_path.write_text("# 旧版本\n\n## 产品概述\n\n旧内容。", encoding="utf-8")
        markdown_path, digest = self.store.write_draft("NEW-20260812-01", MARKDOWN)
        self.draft = DocumentDraft(
            id="DRAFT-001",
            project_id="NEW",
            version_id="NEW-20260812-01",
            parent_version_id="NEW-20260801-01" if with_current else None,
            status=DocumentDraftStatus.PENDING_OWNER,
            markdown_path=markdown_path,
            markdown_sha256=digest,
            source_ids=["SRC-001"],
            section_citations=[
                DocumentSectionCitation(
                    heading="产品概述",
                    source_id="SRC-001",
                    chunk_id="SRC-001-0001",
                    locator="line:1",
                    excerpt="支持 Owner 创建并管理独立项目。",
                )
            ],
            summary="候选摘要",
            missing_sections=[],
            evidence_gaps=[],
            created_at=NOW,
            updated_at=NOW,
        )
        self.drafts = SqliteDocumentDraftRepository(db_path)
        self.drafts.add(self.draft)
        self.manifest = ManifestStore(
            self.paths.manifest_path, project_root=self.paths.project_root
        )
        self.publish = PublishDocumentDraft(
            paths=self.paths,
            projects=self.projects,
            sources=SqliteSourceRepository(db_path),
            drafts=self.drafts,
            store=self.store,
            manifest=self.manifest,
            reconciliation=ReconciliationService(
                manifest_store=self.manifest,
                db_path=db_path,
                project_root=self.paths.project_root,
            ),
            now=lambda: NOW,
        )

    def add_incremental_draft(self, version_id: str) -> DocumentDraft:
        markdown_path, digest = self.store.write_draft(
            version_id,
            MARKDOWN.replace("新产品方案", "新产品方案增量"),
        )
        draft = self.draft.model_copy(
            update={
                "id": f"DRAFT-{version_id}",
                "version_id": version_id,
                "parent_version_id": self.manifest.read_and_validate().current_version,
                "markdown_path": markdown_path,
                "markdown_sha256": digest,
            }
        )
        self.drafts.add(draft)
        return draft


def _command():
    from src.application.dto.documents import PublishDocumentDraftInput

    return PublishDocumentDraftInput(
        project_id="NEW",
        draft_id="DRAFT-001",
        owner_name="产品经理",
        display_version="1.0",
    )


def _incremental_command(draft_id: str):
    from src.application.dto.documents import PublishDocumentDraftInput

    return PublishDocumentDraftInput(
        project_id="NEW",
        draft_id=draft_id,
        owner_name="产品经理",
        display_version="1.1",
    )


def test_initial_publish_creates_first_current_without_parent(tmp_path: Path) -> None:
    env = PublishEnvironment(tmp_path)

    baseline = env.publish.execute(_command())

    version_path = env.paths.wiki_root / "versions" / baseline.version / "产品方案.md"
    assert baseline.parent_baseline_id is None
    assert env.current_path.read_bytes() == version_path.read_bytes()
    assert env.manifest.read_and_validate().current_version == baseline.version
    assert baseline.display_version == "1.0"


def test_manifest_failure_keeps_previous_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = PublishEnvironment(tmp_path)
    env.publish.execute(_command())
    second = env.add_incremental_draft("NEW-20260812-02")
    before = env.current_path.read_bytes()

    def fail_replace(*_args, **_kwargs):
        raise OSError("disk")

    monkeypatch.setattr(env.manifest, "atomic_replace", fail_replace)

    with pytest.raises(Exception, match="RELEASE_FAILED"):
        env.publish.execute(_incremental_command(second.id))

    assert env.current_path.read_bytes() == before
    assert not (env.paths.wiki_root / "versions" / second.version_id).exists()


def test_incremental_publish_advances_current_from_matching_parent(tmp_path: Path) -> None:
    env = PublishEnvironment(tmp_path)
    first = env.publish.execute(_command())
    second = env.add_incremental_draft("NEW-20260812-02")

    baseline = env.publish.execute(_incremental_command(second.id))

    assert baseline.parent_baseline_id == first.id
    assert env.manifest.read_and_validate().current_version == second.version_id
    assert "增量" in env.current_path.read_text(encoding="utf-8")


def test_current_mirror_failure_is_repaired_from_new_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = PublishEnvironment(tmp_path)

    def fail_sync(*_args, **_kwargs):
        raise OSError("disk")

    monkeypatch.setattr(env.store, "sync_current_from_version", fail_sync)

    with pytest.raises(Exception, match="RELEASE_FAILED"):
        env.publish.execute(_command())

    assert env.manifest.read_and_validate().current_version == "NEW-20260812-01"
    version_path = env.paths.wiki_root / "versions" / "NEW-20260812-01" / "产品方案.md"
    assert env.current_path.read_bytes() == version_path.read_bytes()


def test_publish_rejects_citation_from_another_project(tmp_path: Path) -> None:
    env = PublishEnvironment(tmp_path)
    db_path = env.paths.library_root / ".incubator/product_incubator.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE document_drafts SET section_citations_json = ? WHERE id = ?",
            (
                json.dumps(
                    [
                        env.draft.section_citations[0]
                        .model_copy(update={"source_id": "SRC-OTHER"})
                        .model_dump(mode="json")
                    ],
                    ensure_ascii=False,
                ),
                env.draft.id,
            ),
        )

    with pytest.raises(Exception, match="DOCUMENT_CITATION_SOURCE"):
        env.publish.execute(_command())
